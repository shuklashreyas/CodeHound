"""Compare a captured PR's pinned revisions against operator-owned test suites."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from codehound.execution.docker import DockerRunner
from codehound.execution.independent import IndependentRunner
from codehound.execution.profiles import TrustedSuite
from codehound.execution.results import compare_tests, summarize_comparisons
from codehound.repositories.checkout import GitWorkspace
from codehound.repositories.urls import parse_pull_url


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
):
    if mode not in ("pytest", "independent"):
        raise ValueError("Unsupported evaluator mode.")
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
    if on_progress:
        await on_progress("checkout")
    async with workspace_factory(
        reference.repository, snapshot["base_sha"], snapshot["head_sha"]
    ) as checkouts:
        if integrity_analyzer is not None:
            if on_progress:
                await on_progress("test_integrity")
            integrity = await integrity_analyzer(snapshot, checkouts, runner.image_id)
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
    return {
        "schema_version": 2,
        "evaluation_mode": mode,
        "pr_url": reference.url,
        "base_sha": snapshot["base_sha"],
        "head_sha": snapshot["head_sha"],
        "diff_sha256": snapshot["diff_sha256"],
        "suites": results,
        "test_integrity": integrity,
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--visible-tests", required=True, type=Path)
    parser.add_argument("--hidden-tests", type=Path)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--inspect-tests", action="store_true")
    parser.add_argument("--mode", choices=("pytest", "independent"), default="pytest")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists. Choose a new evidence file.")
    if args.snapshot.stat().st_size > 32 * 1024 * 1024:
        parser.error("Snapshot exceeds 32 MiB.")
    runner_class = IndependentRunner if args.mode == "independent" else DockerRunner
    runner = runner_class(args.image_id, timeout_seconds=args.timeout)
    snapshot = json.loads(args.snapshot.read_text())
    suites = {"visible": args.visible_tests}
    if args.hidden_tests:
        suites["hidden"] = args.hidden_tests
    from codehound.execution.integrity import analyze_test_integrity

    result = asyncio.run(
        verify_snapshot(
            snapshot,
            suites,
            runner,
            mode=args.mode,
            integrity_analyzer=analyze_test_integrity if args.inspect_tests else None,
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
