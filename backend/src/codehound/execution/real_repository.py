"""Reproduce an operator-owned historical public PR example in restricted Docker."""

import argparse
import asyncio
import json
from pathlib import Path

from codehound.evaluation.registry import load_profiles
from codehound.evaluation.requirements import requirement_evidence
from codehound.execution.impact import analyze_python_impact
from codehound.execution.independent import IndependentRunner
from codehound.execution.integrity import analyze_test_integrity
from codehound.execution.static_analysis import analyze_static
from codehound.execution.verify import verify_snapshot


async def reproduce(snapshot, profile, image_id, *, progress=None, **overrides):
    if profile.repository.casefold() != snapshot["repository"]["full_name"].casefold():
        raise ValueError("Profile and snapshot repositories differ.")
    options = {
        "mode": "independent",
        "on_progress": progress,
        "integrity_analyzer": analyze_test_integrity,
        "impact_analyzer": analyze_python_impact,
        "static_analyzer": analyze_static,
        "repository_test_config": profile.repository_tests,
    }
    options.update(overrides)
    suites = {"visible": profile.visible}
    if profile.hidden:
        suites["hidden"] = profile.hidden
    artifact = await verify_snapshot(snapshot, suites, IndependentRunner(image_id), **options)
    artifact["evaluation_profile"] = profile.public()
    artifact["requirement_evidence"] = requirement_evidence(profile, artifact)
    artifact["example_provenance"] = snapshot.get("provenance")
    artifact["limitations"].extend(snapshot.get("limitations", []))
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--profile", default="packaging-name-validation")
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new evidence file.")
    if args.snapshot.stat().st_size > 32 * 1024 * 1024:
        parser.error("Snapshot exceeds 32 MiB.")
    profiles = load_profiles()
    if args.profile not in profiles:
        parser.error("Unknown operator profile.")

    async def progress(stage):
        print(stage, flush=True)

    artifact = asyncio.run(
        reproduce(
            json.loads(args.snapshot.read_text()),
            profiles[args.profile],
            args.image_id,
            progress=progress,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:
        json.dump(artifact, output, indent=2, allow_nan=False)
        output.write("\n")
    for name, suite in artifact["suites"].items():
        print(f"{name}: {suite['comparison']}")
    print(f"Evidence saved to {args.output}")


if __name__ == "__main__":
    main()
