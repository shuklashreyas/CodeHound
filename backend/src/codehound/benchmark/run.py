"""Execute an operator-owned benchmark manifest using external JSON assertions."""

import argparse
import asyncio
import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from codehound.benchmark.manifest import prepare_manifest
from codehound.benchmark.metrics import independent_decision, summarize, visible_decision
from codehound.execution.independent import IndependentRunner
from codehound.execution.results import compare_tests, summarize_comparisons
from codehound.execution.verify import suite_digest

LIMITATIONS = [
    "Labels and expectations are operator-supplied, not established by this runner.",
    "Public synthetic fixtures do not estimate detection on real agent patches.",
    "Independent acceptance covers configured contracts only; other behavior is unverified.",
    "No calibrated confidence, LLM judge, or learned classifier is produced.",
]


async def run_benchmark(
    manifest_path, image_id, *, max_seconds=600, runner=None, progress=None, checkpoint=None
):
    if type(max_seconds) is not int or not 1 <= max_seconds <= 7200:
        raise ValueError("Benchmark deadline must be between 1 and 7200 seconds.")
    manifest, manifest_sha, tasks = prepare_manifest(manifest_path)
    runner = runner or IndependentRunner(image_id)
    rows = []
    for task, _, _, _, candidates in tasks:
        for candidate, _, digest in candidates:
            rows.append(
                {
                    "task_id": task.id,
                    "family": task.family,
                    "split": task.split,
                    "candidate_id": candidate.id,
                    "workspace_sha256": digest,
                    "label": candidate.label,
                    "label_reason": candidate.label_reason,
                    "status": "not_run",
                    "decisions": {"visible_only": "abstain", "independent": "abstain"},
                    "assessment": None,
                    "suites": {},
                }
            )
    record = {
        "schema_version": 1,
        "name": manifest.name,
        "dataset_kind": manifest.dataset_kind,
        "provenance": manifest.provenance,
        "manifest_sha256": manifest_sha,
        "image_id": image_id,
        "started_at": datetime.now(UTC).isoformat(),
        "status": "running",
        "tasks": [],
        "rows": rows,
        "limitations": LIMITATIONS,
    }

    def save_checkpoint():
        record["metrics"] = summarize(rows)
        if checkpoint:
            checkpoint(record)

    started = time.monotonic()
    current = None
    cancelled = False
    save_checkpoint()
    try:
        async with asyncio.timeout(max_seconds):
            for task, baseline, baseline_sha, profiles, candidates in tasks:
                task_record = {
                    "id": task.id,
                    "issue": task.issue,
                    "baseline_sha256": baseline_sha,
                    "profile_sha256": {name: profile.sha256 for name, profile in profiles.items()},
                    "baseline": {},
                }
                record["tasks"].append(task_record)
                base_results = {}
                for name, profile in profiles.items():
                    if progress:
                        progress(f"{task.id}: baseline {name}")
                    base_results[name] = await runner.run(baseline, profile)
                    task_record["baseline"][name] = base_results[name].to_dict()
                save_checkpoint()
                if suite_digest(baseline) != baseline_sha:
                    raise ValueError("Baseline changed during benchmark execution.")
                for candidate, workspace, digest in candidates:
                    current = next(
                        row
                        for row in rows
                        if row["task_id"] == task.id and row["candidate_id"] == candidate.id
                    )
                    current["status"] = "running"
                    for name, profile in profiles.items():
                        if progress:
                            progress(f"{task.id}/{candidate.id}: {name}")
                        result = await runner.run(workspace, profile)
                        current["suites"][name] = {
                            "candidate": result.to_dict(),
                            "test_comparison": compare_tests(base_results[name], result),
                        }
                        if name == "visible":
                            current["decisions"]["visible_only"] = visible_decision(result)
                    if suite_digest(workspace) != digest:
                        raise ValueError("Candidate workspace changed during benchmark execution.")
                    current["assessment"] = summarize_comparisons(current["suites"])
                    current["decisions"]["independent"] = independent_decision(
                        current["assessment"]
                    )
                    current["status"] = "evaluated"
                    current = None
                    save_checkpoint()
        record["status"] = "completed"
    except asyncio.CancelledError:
        cancelled = True
        record["status"] = "cancelled"
        if current:
            current["status"] = "interrupted"
    except TimeoutError:
        record["status"] = "timeout"
        if current:
            current["status"] = "interrupted"
    except (ValueError, OSError):
        # A modified dataset invalidates every aggregate, not only the active row.
        record["status"] = "invalidated"
        for row in rows:
            row["status"] = "invalidated"
            row["assessment"] = None
            row["decisions"] = {"visible_only": "abstain", "independent": "abstain"}
        record["error"] = "Dataset identity or execution setup changed; discard these judgments."
    record["finished_at"] = datetime.now(UTC).isoformat()
    record["duration_seconds"] = round(time.monotonic() - started, 3)
    save_checkpoint()
    if cancelled:
        raise asyncio.CancelledError
    return record


def evidence_writer(path):
    """Atomically create a new artifact, then replace only this run's checkpoints."""
    created = False

    def write(record):
        nonlocal created
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", dir=path.parent, prefix=".codehound-benchmark-", delete=False
            ) as output:
                temporary = Path(output.name)
                json.dump(record, output, indent=2, allow_nan=False)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            if created:
                os.replace(temporary, path)
            else:
                os.link(temporary, path)
                created = True
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    return write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-seconds", type=int, default=600)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new evidence file.")
    result = asyncio.run(
        run_benchmark(
            args.manifest,
            args.image_id,
            max_seconds=args.max_seconds,
            progress=lambda message: print(message, flush=True),
            checkpoint=evidence_writer(args.output),
        )
    )
    print(f"Benchmark {result['status']}; evidence: {args.output}")
    for evaluator, metrics in result["metrics"]["visible_test_passing_candidates"].items():
        print(
            f"{evaluator}: detected {metrics['invalid_detected']}/{metrics['invalid_total']} "
            f"invalid visible-passing patches; false positives "
            f"{metrics['valid_rejected']}/{metrics['valid_total']}"
        )
    if result["status"] != "completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
