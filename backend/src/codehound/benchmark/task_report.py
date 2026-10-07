"""Validate and merge disjoint frozen task experiments before human-reviewed scoring."""

import argparse
import hashlib
import re
from dataclasses import fields
from pathlib import Path

from codehound.benchmark.corpus import prepare_corpus
from codehound.benchmark.corpus_metrics import score
from codehound.benchmark.corpus_run import CONTROLLERS, regular_bytes
from codehound.benchmark.review import join_reviews, load_reviews
from codehound.benchmark.run import evidence_writer
from codehound.benchmark.task_experiment import compare_observations, load_profile, observation
from codehound.execution.docker import CONTROLLERS as DOCKER_CONTROLLERS
from codehound.execution.docker import ExecutionResult
from codehound.execution.protocol import load_evidence
from codehound.execution.provenance import bind_source, controller_binding

_SOURCE_BINDING = bind_source(__file__)
REPORT_CONTROLLERS = (
    *CONTROLLERS,
    "benchmark/task_experiment.py",
    "benchmark/task_report.py",
    "benchmark/corpus_metrics.py",
    "benchmark/review.py",
)


def read_manifest(path):
    path = Path(path).absolute()
    raw = regular_bytes(path, 1024 * 1024)
    entries = load_evidence(raw, limit=1024 * 1024)
    if not isinstance(entries, list) or not 1 <= len(entries) <= 20:
        raise ValueError("Manifest must contain 1 through 20 experiment input objects.")
    resolved = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"evidence", "mapping", "adapter"}:
            raise ValueError("Manifest entries require evidence, mapping and adapter paths.")
        if any(
            not isinstance(value, str) or not value or len(value) > 2000 for value in entry.values()
        ):
            raise ValueError("Manifest paths must be bounded nonempty strings.")
        resolved.append({key: path.parent / value for key, value in entry.items()})
    if len({str(entry["evidence"].absolute()) for entry in resolved}) != len(resolved):
        raise ValueError("An experiment artifact cannot be included twice.")
    return resolved, hashlib.sha256(raw).hexdigest()


def validated_observation(saved, expected, image):
    if not isinstance(saved, dict) or not isinstance(saved.get("raw_execution"), dict):
        raise ValueError("Observation requires retained raw execution evidence.")
    raw = saved["raw_execution"]
    if set(raw) - {field.name for field in fields(ExecutionResult)}:
        raise ValueError("Unexpected raw execution fields.")
    try:
        run = ExecutionResult(**raw)
    except TypeError:
        raise ValueError("Malformed raw execution evidence.") from None
    if (
        run.image_id != image
        or run.controller_binding != controller_binding(DOCKER_CONTROLLERS).to_dict()
        or run.network != "none"
        or not isinstance(run.stdout, str)
        or type(run.output_truncated) is not bool
        or type(run.oom_killed) is not bool
    ):
        raise ValueError("Raw execution image/controller/isolation identity differs.")
    frames = [line for line in run.stdout.splitlines() if line.startswith("CODEHOUND_CALL_V1:")]
    if len(frames) > 1:
        raise ValueError("Multiple response frames cannot be accepted as evidence.")
    token = "0" * 32
    if frames:
        match = re.match(r"^CODEHOUND_CALL_V1:([0-9a-f]{32}):", frames[0])
        if match is None:
            raise ValueError("Malformed or unknown response frame.")
        token = match[1]
    recomputed = observation(run, token, expected)
    if {key: value for key, value in saved.items() if key != "workspace_sha256"} != recomputed:
        raise ValueError("Saved observation differs from raw response evidence.")
    if not re.fullmatch(r"[0-9a-f]{64}", str(saved.get("workspace_sha256", ""))):
        raise ValueError("Copied workspace identity is missing or malformed.")
    return recomputed


def validated_record(record, corpus_sha, prepared, specification, mapping_sha, adapter_sha):
    expected_controller = controller_binding((*CONTROLLERS, "benchmark/task_experiment.py"))
    if (
        not isinstance(record, dict)
        or record.get("kind") != "frozen_public_behavior_experiment"
        or type(record.get("schema_version")) is not int
        or record.get("schema_version") != 1
        or record.get("status") not in {"completed", "timeout", "cancelled", "running"}
        or record.get("corpus_sha256") != corpus_sha
        or record.get("mapping_sha256") != mapping_sha
        or record.get("adapter_sha256") != adapter_sha
        or record.get("controller_binding") != expected_controller.to_dict()
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(record.get("image_id", "")))
    ):
        raise ValueError("Experiment artifact identity differs from its frozen inputs.")
    rows = record.get("rows")
    if (
        not isinstance(rows, list)
        or len(rows) != len(prepared)
        or any(not isinstance(row, dict) or not isinstance(row.get("case_id"), str) for row in rows)
    ):
        raise ValueError("Experiment must retain every case as an object.")
    by_id = {row["case_id"]: row for row in rows}
    if len(by_id) != len(rows) or set(by_id) != {item.case.id for item in prepared}:
        raise ValueError("Experiment cases differ or repeat.")
    profiles = {profile.task_id: profile for profile in specification.tasks}
    corpus_tasks = {item.case.task_id: item.case for item in prepared}
    for task_id, profile in profiles.items():
        case = corpus_tasks.get(task_id)
        if case is None or (profile.repository, profile.base_commit, profile.issue_sha256) != (
            case.repository,
            case.base_sha,
            case.issue_sha256,
        ):
            raise ValueError("Profile does not bind a retained task.")
    verified = []
    for item in prepared:
        row = by_id[item.case.id]
        case = item.case
        expected = {
            "task_id": case.task_id,
            "case_identity_sha256": item.identity_sha256,
            "group": case.family,
            "split": case.split,
            "repository": case.repository,
            "base_commit": case.base_sha,
            "patch_sha256": case.patch_sha256,
            "issue_sha256": case.issue_sha256,
        }
        if any(row.get(key) != value for key, value in expected.items()):
            raise ValueError("Experiment row identity differs from the retained case.")
        profile = profiles.get(case.task_id)
        if row.get("status") not in {"evaluated", "abstained"}:
            raise ValueError("Unexpected experiment row status.")
        if profile is not None and row.get("profile_authorship") != profile.authorship.model_dump(
            mode="json"
        ):
            raise ValueError("Profile authorship attribution differs.")
        observations = row.get("observations")
        if not isinstance(observations, dict) or set(observations) - {"baseline", "candidate"}:
            raise ValueError("Unexpected paired observation inventory.")
        supported = profile is not None and profile.supported
        if not supported and (observations or row.get("comparison") is not None):
            raise ValueError("Unsupported profile cannot supply execution evidence.")
        checked = (
            {
                revision: validated_observation(saved, profile.expected, record["image_id"])
                for revision, saved in observations.items()
            }
            if supported
            else {}
        )
        comparison = None
        if set(checked) == {"baseline", "candidate"}:
            comparison = compare_observations(
                checked["baseline"], checked["candidate"], profile.expected
            )
        if row.get("comparison") is not None and row["comparison"] != comparison:
            raise ValueError("Stored comparison differs from raw observations.")
        decision = "abstain"
        if row["status"] == "evaluated":
            if not supported or comparison is None or comparison["decision"] == "abstain":
                raise ValueError("Evaluated row requires usable paired observations.")
            if row.get("comparison") != comparison:
                raise ValueError("Evaluated row is missing its observed comparison.")
            decision = comparison["decision"]
        if row.get("decisions") != {"task_behavior": decision}:
            raise ValueError("Saved decision differs from validated evidence.")
        verified.append(row | {"decisions": {"task_behavior": decision}})
    return verified, profiles


def report(corpus_path, manifest_path, reviews_path):
    binding = controller_binding(REPORT_CONTROLLERS)
    _, corpus_sha, prepared = prepare_corpus(corpus_path)
    entries, manifest_sha = read_manifest(manifest_path)
    merged = {
        item.case.id: {
            "case_id": item.case.id,
            "task_id": item.case.task_id,
            "case_identity_sha256": item.identity_sha256,
            "group": item.case.family,
            "split": item.case.split,
            "repository": item.case.repository,
            "base_commit": item.case.base_sha,
            "status": "abstained",
            "reason": "no_supported_task_profile",
            "decisions": {"task_behavior": "abstain"},
        }
        for item in prepared
    }
    ownership, inputs, retained_bytes = set(), [], 0
    for entry in entries:
        raw = regular_bytes(entry["evidence"], 32 * 1024 * 1024)
        retained_bytes += len(raw)
        if retained_bytes > 64 * 1024 * 1024:
            raise ValueError("Combined experiment evidence exceeds its byte limit.")
        record = load_evidence(raw, limit=32 * 1024 * 1024)
        specification, mapping_sha = load_profile(entry["mapping"])
        if Path(specification.adapter).resolve() != entry["adapter"].resolve():
            raise ValueError("Manifest adapter differs from the profile adapter.")
        adapter_sha = hashlib.sha256(regular_bytes(entry["adapter"], 128 * 1024)).hexdigest()
        rows, profiles = validated_record(
            record, corpus_sha, prepared, specification, mapping_sha, adapter_sha
        )
        for row in rows:
            profile = profiles.get(row["task_id"])
            if profile is not None and profile.supported:
                if row["case_id"] in ownership:
                    raise ValueError("Overlapping supported experiments cannot be cherry-picked.")
                ownership.add(row["case_id"])
                merged[row["case_id"]] = row
        inputs.append(
            {
                "evidence": str(entry["evidence"]),
                "evidence_sha256": hashlib.sha256(raw).hexdigest(),
                "mapping": str(entry["mapping"]),
                "mapping_sha256": mapping_sha,
                "adapter": str(entry["adapter"]),
                "adapter_sha256": adapter_sha,
                "image_id": record["image_id"],
                "status": record["status"],
            }
        )
    reviews = load_reviews(reviews_path)
    labels = {item["case_id"]: item for item in join_reviews(prepared, reviews)}
    rows = [merged[item.case.id] | labels[item.case.id] for item in prepared]
    result = {
        "schema_version": 1,
        "kind": "validated_task_experiment_report",
        "corpus_sha256": corpus_sha,
        "manifest_sha256": manifest_sha,
        "review_sha256": hashlib.sha256(regular_bytes(reviews_path, 4 * 1024 * 1024)).hexdigest(),
        "controller_binding": binding.to_dict(),
        "inputs": inputs,
        "rows": rows,
        "metrics": score(rows, "task_behavior"),
        "by_repository": {
            name: score([row for row in rows if row["repository"] == name], "task_behavior")
            for name in sorted({row["repository"] for row in rows})
        },
        "by_profile_timing": {
            timing: score(
                [
                    row
                    for row in rows
                    if (row.get("profile_authorship") or {}).get("timing", "unmapped") == timing
                ],
                "task_behavior",
            )
            for timing in ("pre_patch", "post_patch", "unmapped")
        },
        "limitations": [
            "Selected task behaviors do not establish full patch correctness.",
            "Adapter observations share a process with candidate code; evidence is unsigned.",
            "Only agreed retained human reviews produce accuracy labels.",
            "No supported experiment may overlap another supported experiment.",
        ],
    }
    binding.ensure_current()
    if (
        prepare_corpus(corpus_path)[1] != corpus_sha
        or read_manifest(manifest_path)[1] != manifest_sha
    ):
        raise ValueError("Corpus or manifest changed during reporting.")
    for item in inputs:
        if (
            hashlib.sha256(regular_bytes(item["evidence"], 32 * 1024 * 1024)).hexdigest(),
            load_profile(item["mapping"])[1],
            hashlib.sha256(regular_bytes(item["adapter"], 128 * 1024)).hexdigest(),
        ) != (item["evidence_sha256"], item["mapping_sha256"], item["adapter_sha256"]):
            raise ValueError("Experiment inputs changed during reporting.")
    if load_reviews(reviews_path) != reviews:
        raise ValueError("Human reviews changed during reporting.")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("corpus", "manifest", "reviews", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    result = report(args.corpus, args.manifest, args.reviews)
    evidence_writer(args.output)(result)
    print(f"Validated {len(result['rows'])} cases; report: {args.output}")


if __name__ == "__main__":
    main()
