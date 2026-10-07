"""Operator-frozen public-behavior experiments; no ground-truth labels are loaded."""

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import uuid4

from pydantic import Field, JsonValue, model_validator

from codehound.benchmark.corpus import Digest, prepare_corpus
from codehound.benchmark.corpus_run import (
    CONTROLLERS,
    apply_patch,
    copy_revision,
    regular_bytes,
    workspace_digest,
)
from codehound.benchmark.run import evidence_writer
from codehound.execution.docker import ContainerRunner
from codehound.execution.independent import decode_response
from codehound.execution.profiles import Contract
from codehound.execution.protocol import load_evidence
from codehound.execution.provenance import bind_source, controller_binding
from codehound.repositories.checkout import CheckoutFailure, GitWorkspace

_SOURCE_BINDING = bind_source(__file__)


class Authorship(Contract):
    scope: Literal["development"]
    timing: Literal["pre_patch", "post_patch"]
    basis: str = Field(min_length=10, max_length=4000)


class TaskProfile(Contract):
    task_id: str = Field(min_length=1, max_length=200)
    repository: str = Field(min_length=3, max_length=200)
    base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    issue_sha256: Digest
    supported: bool
    unsupported_reason: str | None
    expected: dict[str, JsonValue]
    authorship: Authorship
    notes: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def supported_contract(self):
        if self.supported:
            if not 1 <= len(self.expected) <= 30 or self.unsupported_reason is not None:
                raise ValueError(
                    "Supported profiles need bounded scenarios and no abstention reason."
                )
        elif self.expected or not self.unsupported_reason:
            raise ValueError("Unsupported profiles need a reason and no expected observations.")
        if any(not key or len(key) > 150 for key in self.expected):
            raise ValueError("Scenario identifiers must be bounded and nonempty.")
        return self


class Experiment(Contract):
    schema_version: int = Field(ge=1, le=1)
    name: str = Field(min_length=1, max_length=200)
    adapter: str = Field(min_length=1, max_length=500)
    purpose: Literal["development_pilot"]
    tasks: list[TaskProfile] = Field(min_length=1, max_length=100)
    limitations: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def unique_tasks(self):
        if len({task.task_id for task in self.tasks}) != len(self.tasks):
            raise ValueError("Duplicate task profile.")
        return self


def load_profile(path):
    raw = regular_bytes(path, 2 * 1024 * 1024)
    return Experiment.model_validate(load_evidence(raw, limit=2 * 1024 * 1024)), hashlib.sha256(
        raw
    ).hexdigest()


def observation(run, token, expected):
    response, error = decode_response(run.stdout, token)
    usable = (
        run.status == "completed"
        and run.exit_code == 0
        and not run.output_truncated
        and not run.oom_killed
        and error is None
        and isinstance(response, dict)
        and response.get("kind") == "returned"
        and isinstance(response.get("value"), dict)
        and set(response["value"]) == set(expected)
    )
    return {
        "raw_execution": run.to_dict(),
        "response": response,
        "decode_error": error,
        "usable": usable,
        "values": response["value"] if usable else None,
    }


def compare_observations(baseline, candidate, expected):
    if not baseline["usable"] or not candidate["usable"]:
        return {"decision": "abstain", "reason": "observation_unavailable", "scenarios": {}}
    scenarios = {
        name: {
            "expected": value,
            "baseline_observed": baseline["values"][name],
            "candidate_observed": candidate["values"][name],
            "baseline_matches": same_json(baseline["values"][name], value),
            "candidate_matches": same_json(candidate["values"][name], value),
        }
        for name, value in expected.items()
    }
    return {
        "decision": "accept"
        if all(s["candidate_matches"] for s in scenarios.values())
        else "reject",
        "reason": "selected_task_behaviors_only",
        "scenarios": scenarios,
        "improvements": [
            k for k, v in scenarios.items() if not v["baseline_matches"] and v["candidate_matches"]
        ],
        "regressions": [
            k for k, v in scenarios.items() if v["baseline_matches"] and not v["candidate_matches"]
        ],
        "unresolved": [
            k
            for k, v in scenarios.items()
            if not v["baseline_matches"] and not v["candidate_matches"]
        ],
    }


def same_json(left, right):
    """Compare JSON values without treating booleans as numbers."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) or isinstance(right, dict):
        return (
            isinstance(left, dict)
            and isinstance(right, dict)
            and set(left) == set(right)
            and all(same_json(left[key], right[key]) for key in left)
        )
    if isinstance(left, list) or isinstance(right, list):
        return (
            isinstance(left, list)
            and isinstance(right, list)
            and len(left) == len(right)
            and all(same_json(a, b) for a, b in zip(left, right, strict=True))
        )
    return left == right


async def run_experiment(
    corpus_path,
    mapping_path,
    adapter_path,
    image_id,
    *,
    max_seconds=1200,
    runner=None,
    workspace_factory=GitWorkspace,
    checkpoint=None,
    progress=None,
):
    if type(max_seconds) is not int or not 1 <= max_seconds <= 3600:
        raise ValueError("Experiment budget must be 1 through 3600 seconds.")
    runner = runner or ContainerRunner(image_id, timeout_seconds=30, output_limit=65536)
    controller = controller_binding((*CONTROLLERS, "benchmark/task_experiment.py"))
    _, corpus_sha, prepared = prepare_corpus(corpus_path)
    specification, mapping_sha = load_profile(mapping_path)
    if Path(specification.adapter).resolve() != Path(adapter_path).resolve():
        raise ValueError("The supplied adapter differs from the frozen profile.")
    script = regular_bytes(adapter_path, 128 * 1024)
    adapter_sha = hashlib.sha256(script).hexdigest()
    profiles = {task.task_id: task for task in specification.tasks}
    tasks = {item.case.task_id: item.case for item in prepared}
    for name, profile in profiles.items():
        case = tasks.get(name)
        if case is None or (profile.repository, profile.base_commit, profile.issue_sha256) != (
            case.repository,
            case.base_sha,
            case.issue_sha256,
        ):
            raise ValueError("Profile does not bind an exact retained task.")
    rows = [
        {
            "case_id": item.case.id,
            "task_id": item.case.task_id,
            "case_identity_sha256": item.identity_sha256,
            "group": item.case.family,
            "split": item.case.split,
            "repository": item.case.repository,
            "base_commit": item.case.base_sha,
            "patch_sha256": item.case.patch_sha256,
            "issue_sha256": item.case.issue_sha256,
            "status": "abstained",
            "reason": "no_task_profile",
            "decisions": {"task_behavior": "abstain"},
            "observations": {},
        }
        for item in prepared
    ]
    record = {
        "schema_version": 1,
        "kind": "frozen_public_behavior_experiment",
        "status": "running",
        "name": specification.name,
        "purpose": "development_pilot",
        "corpus_sha256": corpus_sha,
        "mapping_sha256": mapping_sha,
        "adapter_sha256": adapter_sha,
        "image_id": image_id,
        "controller_binding": controller.to_dict(),
        "profile_limitations": specification.limitations,
        "rows": rows,
        "started_at": datetime.now(UTC).isoformat(),
        "limitations": [
            "Accept means the selected behavioral probes passed, not complete task correctness.",
            "No human labels are loaded during execution. Undefined accuracy remains undefined.",
            "Pre-patch and post-patch development profiles must be reported separately.",
            "Adapter observations share a process with candidate code; "
            "expected values stay outside.",
            "Evidence is unsigned operator-controlled data, "
            "not a cryptographic execution attestation.",
        ],
    }

    def save():
        record["counts"] = {
            status: sum(row["status"] == status for row in rows)
            for status in {row["status"] for row in rows}
        }
        if checkpoint:
            checkpoint(record)

    save()
    timer = asyncio.timeout(max_seconds)
    try:
        async with timer:
            for item, row in zip(prepared, rows, strict=True):
                profile = profiles.get(item.case.task_id)
                if profile is None:
                    continue
                row["profile_authorship"] = profile.authorship.model_dump(mode="json")
                row["profile_notes"] = profile.notes
                if not profile.supported:
                    row["reason"] = profile.unsupported_reason
                    save()
                    continue
                row["reason"] = "not_run"
                if progress:
                    progress(item.case.id)
                try:
                    async with workspace_factory(
                        item.case.repository, item.case.base_sha, item.case.base_sha
                    ) as retained:
                        with TemporaryDirectory(prefix="codehound-task-experiment-") as directory:
                            root = Path(directory)
                            copy_revision(retained.baseline, root / "baseline")
                            copy_revision(retained.baseline, root / "candidate")
                            await apply_patch(root / "candidate", item.patch)
                            (root / "probe").mkdir()
                            (root / "probe" / "observe.py").write_bytes(script)
                            for revision in ("baseline", "candidate"):
                                workspace = root / revision
                                before = workspace_digest(workspace)
                                token = uuid4().hex
                                result = await runner.run_container(
                                    workspace,
                                    [(root / "probe", "/probe")],
                                    ["python", "-I", "/probe/observe.py", token, item.case.task_id],
                                )
                                row["observations"][revision] = observation(
                                    result, token, profile.expected
                                )
                                row["observations"][revision]["workspace_sha256"] = before
                                if workspace_digest(workspace) != before:
                                    raise ValueError("Workspace changed during execution.")
                                save()
                            comparison = compare_observations(
                                row["observations"]["baseline"],
                                row["observations"]["candidate"],
                                profile.expected,
                            )
                            row["comparison"] = comparison
                            row["decisions"]["task_behavior"] = comparison["decision"]
                            row["reason"] = comparison["reason"]
                            row["status"] = (
                                "evaluated" if comparison["decision"] != "abstain" else "abstained"
                            )
                except TimeoutError:
                    row.update(
                        status="abstained",
                        reason="setup_or_observation_timeout",
                        decisions={"task_behavior": "abstain"},
                    )
                    row.pop("comparison", None)
                except (ValueError, OSError, CheckoutFailure) as error:
                    row.update(
                        status="abstained",
                        reason="unsupported_setup_or_patch",
                        decisions={"task_behavior": "abstain"},
                    )
                    row.pop("comparison", None)
                    row["setup_error_type"] = type(error).__name__
                    row["setup_error"] = str(error)[:500]
                save()
        record["status"] = "completed"
    except TimeoutError:
        if not timer.expired():
            raise
        record["status"] = "timeout"
    except asyncio.CancelledError:
        record["status"] = "cancelled"
        save()
        raise
    try:
        controller.ensure_current()
        if (
            prepare_corpus(corpus_path)[1],
            load_profile(mapping_path)[1],
            regular_bytes(adapter_path, 128 * 1024),
        ) != (corpus_sha, mapping_sha, script):
            raise ValueError("Frozen experiment inputs changed.")
    except Exception:
        record["status"] = "invalidated"
        for row in rows:
            row.update(
                status="abstained",
                reason="inputs_or_evaluator_changed",
                decisions={"task_behavior": "abstain"},
            )
    record["finished_at"] = datetime.now(UTC).isoformat()
    save()
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("corpus", "mapping", "adapter", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--max-seconds", type=int, default=1200)
    args = parser.parse_args()
    result = asyncio.run(
        run_experiment(
            args.corpus,
            args.mapping,
            args.adapter,
            args.image_id,
            max_seconds=args.max_seconds,
            checkpoint=evidence_writer(args.output),
            progress=lambda value: print(value, flush=True),
        )
    )
    print(f"{result['status']}: {json.dumps(result['counts'], sort_keys=True)}; {args.output}")
    if result["status"] != "completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
