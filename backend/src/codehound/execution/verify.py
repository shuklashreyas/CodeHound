"""Compare a captured PR's pinned revisions against operator-owned test suites."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from codehound.execution.docker import DockerRunner
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
    if any(
        result.status != "completed" or result.exit_code not in (0, 1)
        for result in (baseline, candidate)
    ):
        return "inconclusive"
    return {
        (0, 0): "both_pass",
        (0, 1): "regression_detected",
        (1, 0): "candidate_improves",
        (1, 1): "both_fail",
    }[baseline.exit_code, candidate.exit_code]


async def verify_snapshot(snapshot, suites, runner, *, workspace_factory=GitWorkspace):
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
    identities = {name: suite_digest(path) for name, path in suites.items()}
    results = {}
    async with workspace_factory(
        reference.repository, snapshot["base_sha"], snapshot["head_sha"]
    ) as checkouts:
        for name, tests in suites.items():
            baseline = await runner.run(checkouts.baseline, tests)
            candidate = await runner.run(checkouts.candidate, tests)
            if suite_digest(tests) != identities[name]:
                raise ValueError("Independent tests changed during execution; discard this run.")
            results[name] = {
                "test_suite_sha256": identities[name],
                "baseline": baseline.to_dict(),
                "candidate": candidate.to_dict(),
                "comparison": comparison(baseline, candidate),
            }
    return {
        "schema_version": 1,
        "pr_url": reference.url,
        "base_sha": snapshot["base_sha"],
        "head_sha": snapshot["head_sha"],
        "diff_sha256": snapshot["diff_sha256"],
        "suites": results,
        "confidence": None,
        "limitations": [
            "Only the supplied operator-owned Python test suites were executed.",
            "Passing tests do not establish full requirement coverage or patch integrity.",
            "Tests share a Python process with candidate code; exit codes can be manipulated.",
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
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists. Choose a new evidence file.")
    if args.snapshot.stat().st_size > 32 * 1024 * 1024:
        parser.error("Snapshot exceeds 32 MiB.")
    runner = DockerRunner(args.image_id, timeout_seconds=args.timeout)
    snapshot = json.loads(args.snapshot.read_text())
    suites = {"visible": args.visible_tests}
    if args.hidden_tests:
        suites["hidden"] = args.hidden_tests
    result = asyncio.run(verify_snapshot(snapshot, suites, runner))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    for name, suite in result["suites"].items():
        print(f"{name}: {suite['comparison']}")
    print(f"Evidence saved to {args.output}")


if __name__ == "__main__":
    main()
