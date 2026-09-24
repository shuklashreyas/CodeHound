"""Run the trusted pagination fixture in Docker and write a reproducible evidence report."""

import argparse
import asyncio
import json
from pathlib import Path

from codehound.execution.docker import DockerRunner


async def run_demo(image_id: str, fixtures: Path):
    runner = DockerRunner(image_id)
    evidence = {}
    for variant in ("original", "correct", "overfit"):
        evidence[variant] = {}
        for suite in ("visible", "hidden"):
            result = await runner.run(fixtures / variant, fixtures / suite)
            evidence[variant][suite] = result.to_dict()
    return {
        "schema_version": 1,
        "fixture": "pagination",
        "execution_evidence": evidence,
        "limitations": [
            "Public, intentionally constructed fixture; not a benchmark score.",
            "Process exit codes do not prove resistance to malicious test interference.",
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
    args = parser.parse_args()
    result = asyncio.run(run_demo(args.image, args.fixtures.resolve()))
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
