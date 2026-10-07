"""POST-HOC public API exploration; never ground truth or benchmark accuracy evidence."""

import argparse
import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from codehound.benchmark.corpus import prepare_corpus
from codehound.benchmark.corpus_run import CONTROLLERS, apply_patch, copy_revision, workspace_digest
from codehound.benchmark.run import evidence_writer
from codehound.execution.docker import ContainerRunner
from codehound.execution.independent import decode_response
from codehound.execution.provenance import bind_source, controller_binding
from codehound.repositories.checkout import GitWorkspace

_SOURCE_BINDING = bind_source(__file__)
CASE_ID = "psf__requests-1921"
ADAPTER = Path(__file__).parents[4] / "benchmarks/probes/requests_1921_adapter.py"
EXPECTED = {
    "session_none": {"x-preserved": "session"},
    "request_none": {"x-preserved": "session"},
    "override": {"x-preserved": "session", "x-test": "request"},
    "issue_example_default_session": {
        "accept_encoding_present": False,
        "accept_present": True,
        "accept_unchanged": True,
    },
}


def compare_observation(run, token):
    response, error = decode_response(run.stdout, token)
    completed = (
        run.status == "completed"
        and run.exit_code == 0
        and not run.output_truncated
        and not run.oom_killed
        and error is None
        and response is not None
        and response.get("kind") == "returned"
    )
    value = response.get("value") if completed else None
    return {
        "response": response,
        "decode_error": error,
        "comparison_status": "observed" if completed else "unavailable",
        "expected_headers": EXPECTED,
        "observed_headers": value,
        "matches_expected": value == EXPECTED if completed else None,
        "scenario_matches": {
            name: isinstance(value, dict) and value.get(name) == expected
            for name, expected in EXPECTED.items()
        }
        if completed
        else None,
    }


async def diagnose(corpus_path, image_id, *, runner=None, workspace_factory=GitWorkspace):
    controller = controller_binding(
        (
            *CONTROLLERS,
            "benchmark/requests_1921_diagnostic.py",
            "execution/independent.py",
        )
    )
    _, corpus_sha, prepared = prepare_corpus(corpus_path)
    selected = [item for item in prepared if item.case.id == CASE_ID]
    if len(selected) != 1:
        raise ValueError("The retained Requests 1921 case must occur exactly once.")
    item = selected[0]
    script = ADAPTER.read_bytes()
    script_sha = hashlib.sha256(script).hexdigest()
    runner = runner or ContainerRunner(image_id, timeout_seconds=30, output_limit=32768)
    observations = {}
    async with workspace_factory(
        item.case.repository, item.case.base_sha, item.case.base_sha
    ) as retained:
        with TemporaryDirectory(prefix="codehound-requests-public-probe-") as temporary:
            root = Path(temporary)
            copy_revision(retained.baseline, root / "baseline")
            copy_revision(retained.baseline, root / "candidate")
            await apply_patch(root / "candidate", item.patch)
            (root / "probe").mkdir()
            (root / "probe" / "observe.py").write_bytes(script)
            for name in ("baseline", "candidate"):
                workspace = root / name
                before = workspace_digest(workspace)
                token = uuid4().hex
                run = await runner.run_container(
                    workspace,
                    [(root / "probe", "/probe")],
                    ["python", "-I", "/probe/observe.py", token],
                )
                if workspace_digest(workspace) != before:
                    raise ValueError("Diagnostic workspace changed during observation.")
                observations[name] = {
                    "workspace_sha256": before,
                    "raw_execution": run.to_dict(),
                    "observation": compare_observation(run, token),
                }
    controller.ensure_current()
    if ADAPTER.read_bytes() != script:
        raise ValueError("Post-hoc observation adapter changed.")
    if prepare_corpus(corpus_path)[1] != corpus_sha:
        raise ValueError("Retained corpus changed during exploration.")
    return {
        "schema_version": 1,
        "kind": "post_hoc_public_api_exploration",
        "authorship": (
            "Authored after inspection of the candidate patch and frozen profile outcome."
        ),
        "accuracy_eligible": False,
        "human_ground_truth": "Not provided; no human verdict is inferred.",
        "frozen_benchmark_profile_modified": False,
        "case_id": item.case.id,
        "case_identity_sha256": item.identity_sha256,
        "corpus_sha256": corpus_sha,
        "repository": item.case.repository,
        "base_sha": item.case.base_sha,
        "issue_sha256": item.case.issue_sha256,
        "patch_sha256": item.case.patch_sha256,
        "image_id": image_id,
        "script_sha256": script_sha,
        "controller_binding": controller.to_dict(),
        "observed_at": datetime.now(UTC).isoformat(),
        "public_api": "Session.prepare_request(Request(...)); requests are never sent.",
        "observations": observations,
        "limitations": [
            "Post-hoc exploration is excluded from accuracy and human review labels.",
            "Selected preparation examples cannot establish complete task correctness.",
            "Python-to-JSON observations share a process with candidate code; "
            "expectations and comparison remain on the host.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    record = asyncio.run(diagnose(args.corpus, args.image_id))
    evidence_writer(args.output)(record)
    for name, observation in record["observations"].items():
        outcome = observation["observation"]
        print(
            f"{name}: {outcome['comparison_status']}; "
            f"matches_expected={outcome['matches_expected']}"
        )
    print(f"Post-hoc exploration retained at {args.output}; excluded from accuracy.")


if __name__ == "__main__":
    main()
