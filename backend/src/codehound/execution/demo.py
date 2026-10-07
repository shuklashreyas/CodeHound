"""Run the trusted pagination fixture in Docker and write a reproducible evidence report."""

import argparse
import asyncio
import json
from pathlib import Path

from codehound.execution.docker import DockerRunner
from codehound.execution.independent import IndependentRunner
from codehound.execution.profiles import TrustedSuite
from codehound.execution.results import compare_tests, summarize_comparisons


async def run_demo(image_id: str, fixtures: Path, mode="pytest"):
    runner = IndependentRunner(image_id) if mode == "independent" else DockerRunner(image_id)
    evidence = {}
    runs = {}
    for variant in ("original", "correct", "overfit", "regressive"):
        evidence[variant] = {}
        runs[variant] = {}
        for suite in ("visible", "hidden"):
            tests = (
                TrustedSuite.load(fixtures / "profiles" / f"{suite}.json")
                if mode == "independent"
                else fixtures / suite
            )
            result = await runner.run(fixtures / variant, tests)
            evidence[variant][suite] = result.to_dict()
            runs[variant][suite] = result
    comparisons = {}
    for variant in ("correct", "overfit", "regressive"):
        suites = {
            suite: {"test_comparison": compare_tests(runs["original"][suite], result)}
            for suite, result in runs[variant].items()
        }
        comparisons[variant] = {"suites": suites, "assessment": summarize_comparisons(suites)}
    return {
        "schema_version": 2,
        "fixture": "pagination",
        "evaluation_mode": mode,
        "execution_evidence": evidence,
        "comparisons": comparisons,
        "limitations": [
            "Public, intentionally constructed fixture; not a benchmark score.",
            (
                "Process exit codes do not prove resistance to malicious test interference."
                if mode == "pytest"
                else "Only the supplied JSON function behavior is verified."
            ),
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Trusted local image ID, sha256:...")
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path(__file__).resolve().parents[3] / "fixtures" / "pagination",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("pytest", "independent"), default="pytest")
    args = parser.parse_args()
    result = asyncio.run(run_demo(args.image, args.fixtures.resolve(), args.mode))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Execution evidence saved to {args.output}")
    for variant, suites in result["execution_evidence"].items():
        print(
            variant,
            {
                name: {"status": item["status"], "exit_code": item["exit_code"]}
                for name, item in suites.items()
            },
        )


if __name__ == "__main__":
    main()
