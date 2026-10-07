"""Compare a captured PR's pinned revisions against operator-owned test suites."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from codehound.execution.deadlines import current_budget, optional_stage
from codehound.execution.docker import DockerRunner
from codehound.execution.independent import IndependentRunner
from codehound.execution.profiles import TrustedSuite
from codehound.execution.repository_tests import RepositoryTestConfig, run_repository_tests
from codehound.execution.results import compare_tests, summarize_comparisons
from codehound.repositories.checkout import GitWorkspace
from codehound.repositories.urls import parse_pull_url

DEADLINE_REASON = "execution_work_budget_exceeded"


def incomplete_inspection(stage, image_id):
    """Keep timed-out optional evidence explicit and compatible with evidence consumers."""
    limitation = "Execution work budget exceeded; this stage was not fully inspected."
    common = {"status": "inconclusive", "image_id": image_id, "limitations": [limitation]}
    if stage == "test_integrity":
        return common | {
            "files_examined": 0,
            "findings": [],
            "unverified": [{"revision": "both", "reason": DEADLINE_REASON}],
        }
    if stage == "repository_impact":
        return common | {
            "revisions": {},
            "new_syntax_errors": [],
            "unverified": [{"revision": "both", "path": "", "reason": DEADLINE_REASON}],
            "unverified_count": 1,
        }
    if stage == "static_analysis":
        from codehound.execution.static_analysis import CONFIG_SHA256, LIMITS, VERSION

        def revision():
            return {
                "status": "inconclusive",
                "files": [],
                "findings": [],
                "skipped_files": [],
                "errors": [DEADLINE_REASON],
                "source_bytes": 0,
            }

        sources = [
            Path(__file__).parent / "static_analysis.py",
            Path(__file__).parent / "inspection" / "static_ruff.py",
        ]
        return common | {
            "tool": {
                "name": "ruff",
                "version": VERSION,
                "rules": ["E", "F"],
                "config_sha256": CONFIG_SHA256,
            },
            "evaluator_sha256": hashlib.sha256(
                b"".join(path.read_bytes() for path in sources)
            ).hexdigest(),
            "baseline": revision(),
            "candidate": revision(),
            "new": [],
            "resolved": [],
            "existing": [],
            "counts": {"new": 0, "resolved": 0, "existing": 0},
            "coverage": {
                "baseline_files": 0,
                "candidate_files": 0,
                "excluded_directories": [".git"],
                "comparison_complete": False,
            },
            "limits": LIMITS,
        }
    if stage == "repository_tests":
        from codehound.execution.repository_tests import LIMITATIONS, SOURCE

        return common | {
            "mode": "frozen_baseline",
            "trust": "repository_controlled",
            "evidence_source": SOURCE,
            "affects_assessment": False,
            "provenance": None,
            "baseline": None,
            "candidate": None,
            "comparison": "inconclusive",
            "test_comparison": None,
            "error_code": DEADLINE_REASON,
            "limitations": [*LIMITATIONS, limitation],
        }
    raise ValueError("Unknown optional inspection stage.")


def suite_digest(directory: Path):
    """Fingerprint trusted test inputs; reject links and bound input size."""
    directory = directory.resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("Test suite must be a directory.")
    digest = hashlib.sha256()
    count = size = 0
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Independent test suites may not contain symlinks.")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Independent test suites must contain regular files only.")
        count += 1
        size += path.stat().st_size
        if count > 10000 or size > 32 * 1024 * 1024:
            raise ValueError("Independent test suite exceeds 10000 files or 32 MiB.")
        relative = path.relative_to(directory).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    if not count:
        raise ValueError("Independent test suite is empty.")
    return digest.hexdigest()


def comparison(baseline, candidate):
    """Describe observed test transitions without claiming full task correctness."""
    return compare_tests(baseline, candidate)["verdict"]


async def verify_snapshot(
    snapshot,
    suites,
    runner,
    *,
    workspace_factory=GitWorkspace,
    mode="pytest",
    on_progress=None,
    integrity_analyzer=None,
    impact_analyzer=None,
    static_analyzer=None,
    repository_test_config=None,
    repository_test_runner=None,
):
    if mode not in ("pytest", "independent"):
        raise ValueError("Unsupported evaluator mode.")
    if repository_test_config is not None:
        repository_test_config = RepositoryTestConfig.model_validate(repository_test_config)
    if snapshot.get("schema_version") != 1:
        raise ValueError("Unsupported snapshot schema.")
    reference = parse_pull_url(snapshot["pull_request"]["url"])
    if reference.repository.casefold() != snapshot["repository"]["full_name"].casefold():
        raise ValueError("Snapshot repository and PR URL differ.")
    if snapshot["repository"]["private"] is not False:
        raise ValueError("Only public repositories are supported.")
    if hashlib.sha256(snapshot["diff"].encode()).hexdigest() != snapshot["diff_sha256"]:
        raise ValueError("Snapshot diff checksum does not match.")
    if not suites or set(suites) - {"visible", "hidden"}:
        raise ValueError("Provide visible and/or independent hidden tests.")

    def identity(path):
        if isinstance(path, TrustedSuite):
            return path.sha256
        return TrustedSuite.load(path).sha256 if mode == "independent" else suite_digest(path)

    identities = {name: identity(path) for name, path in suites.items()}
    prepared = {
        name: (path if isinstance(path, TrustedSuite) else TrustedSuite.load(path))
        if mode == "independent"
        else path
        for name, path in suites.items()
    }
    results = {}
    integrity = None
    impact = None
    static = None
    repository_tests = None
    budget = current_budget()
    if on_progress:
        await on_progress("checkout")
    async with workspace_factory(
        reference.repository, snapshot["base_sha"], snapshot["head_sha"]
    ) as checkouts:

        async def inspections():
            evidence = []
            for stage, analyzer in (
                ("test_integrity", integrity_analyzer),
                ("repository_impact", impact_analyzer),
                ("static_analysis", static_analyzer),
            ):
                result = None
                if analyzer is not None:
                    if on_progress:
                        await on_progress(stage)
                    result = await optional_stage(
                        stage,
                        lambda: analyzer(snapshot, checkouts, runner.image_id),
                        lambda: incomplete_inspection(stage, runner.image_id),
                    )
                evidence.append(result)
            return evidence

        # Standalone callers retain their original order and suite limits. Workers
        # prioritize independent evidence before spending their optional-stage budget.
        if budget is None:
            integrity, impact, static = await inspections()
        for name, tests in suites.items():
            if on_progress:
                await on_progress(f"{name}_baseline")
            baseline = await runner.run(checkouts.baseline, prepared[name])
            if on_progress:
                await on_progress(f"{name}_candidate")
            candidate = await runner.run(checkouts.candidate, prepared[name])
            if identity(tests) != identities[name]:
                raise ValueError("Independent tests changed during execution; discard this run.")
            results[name] = {
                "test_suite_sha256": identities[name],
                "baseline": baseline.to_dict(),
                "candidate": candidate.to_dict(),
                "comparison": comparison(baseline, candidate),
                "test_comparison": compare_tests(baseline, candidate),
            }
        if budget is not None:
            integrity, impact, static = await inspections()
        if repository_test_config is not None:
            repository_tests = await optional_stage(
                "repository_tests",
                lambda: run_repository_tests(
                    checkouts,
                    repository_test_config,
                    runner.image_id,
                    runner=repository_test_runner,
                    on_progress=on_progress,
                ),
                lambda: incomplete_inspection("repository_tests", runner.image_id),
            )
    artifact = {
        "schema_version": 2,
        "evaluation_mode": mode,
        "pr_url": reference.url,
        "base_sha": snapshot["base_sha"],
        "head_sha": snapshot["head_sha"],
        "diff_sha256": snapshot["diff_sha256"],
        "suites": results,
        "test_integrity": integrity,
        "python_impact": impact,
        "static_analysis": static,
        "repository_tests": repository_tests,
        "assessment": summarize_comparisons(results),
        "confidence": None,
        "limitations": [
            "Only the supplied operator-owned Python test suites were executed.",
            "Passing tests do not establish full requirement coverage or patch integrity.",
            (
                "Tests share a Python process with candidate code; reports can be manipulated."
                if mode == "pytest"
                else "Only configured JSON function behavior is checked."
            ),
            "Repository dependencies must already be present in the trusted image.",
        ],
    }
    if budget is not None:
        artifact["execution_budget"] = budget.to_dict()
        if budget.incomplete_stages:
            artifact["limitations"].append(
                "Execution work budget exceeded. Completed independent observations are retained; "
                "unfinished cases and optional stages remain unverified."
            )
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--visible-tests", required=True, type=Path)
    parser.add_argument("--hidden-tests", type=Path)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--inspect-tests", action="store_true")
    parser.add_argument("--inspect-python", action="store_true")
    parser.add_argument("--repository-test-config", type=Path)
    parser.add_argument("--inspect-static", action="store_true")
    parser.add_argument("--mode", choices=("pytest", "independent"), default="pytest")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists. Choose a new evidence file.")
    if args.snapshot.stat().st_size > 32 * 1024 * 1024:
        parser.error("Snapshot exceeds 32 MiB.")
    runner_class = IndependentRunner if args.mode == "independent" else DockerRunner
    runner = runner_class(args.image_id, timeout_seconds=args.timeout)
    snapshot = json.loads(args.snapshot.read_text())
    repository_config = None
    if args.repository_test_config:
        if args.repository_test_config.stat().st_size > 16 * 1024:
            parser.error("Repository test configuration exceeds 16 KiB.")
        repository_config = RepositoryTestConfig.model_validate_json(
            args.repository_test_config.read_bytes()
        )
    suites = {"visible": args.visible_tests}
    if args.hidden_tests:
        suites["hidden"] = args.hidden_tests
    from codehound.execution.impact import analyze_python_impact
    from codehound.execution.integrity import analyze_test_integrity
    from codehound.execution.static_analysis import analyze_static

    result = asyncio.run(
        verify_snapshot(
            snapshot,
            suites,
            runner,
            mode=args.mode,
            integrity_analyzer=analyze_test_integrity if args.inspect_tests else None,
            impact_analyzer=analyze_python_impact if args.inspect_python else None,
            repository_test_config=repository_config,
            static_analyzer=analyze_static if args.inspect_static else None,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    for name, suite in result["suites"].items():
        print(f"{name}: {suite['comparison']}")
    print(f"Evidence saved to {args.output}")


if __name__ == "__main__":
    main()
