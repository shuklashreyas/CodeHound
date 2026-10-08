"""Offline report fixtures exercise validation, never supply real benchmark labels."""

import asyncio
import base64
import json

import pytest
from test_corpus_run import inputs  # noqa: F401 - shared isolated data-only checkout fixture

from codehound.benchmark.corpus import prepare_corpus
from codehound.benchmark.task_experiment import run_experiment
from codehound.benchmark.task_report import report
from codehound.execution.docker import CONTROLLERS as DOCKER_CONTROLLERS
from codehound.execution.docker import ExecutionResult
from codehound.execution.provenance import controller_binding

IMAGE = "sha256:" + "a" * 64


class RawRunner:
    async def run_container(self, workspace, mounts, command):
        value = int((workspace / "example.py").read_text().split("=")[1])
        token = command[-2]
        payload = base64.b64encode(
            json.dumps({"kind": "returned", "value": {"value": value}}).encode()
        ).decode()
        return ExecutionResult(
            "completed",
            0,
            f"CODEHOUND_CALL_V1:{token}:{payload}",
            "",
            0.01,
            IMAGE,
            False,
            False,
            30,
            controller_binding=controller_binding(DOCKER_CONTROLLERS).to_dict(),
        )


@pytest.fixture
def artifacts(tmp_path, request):
    corpus, _, workspace = request.getfixturevalue("inputs")
    _, _, prepared = prepare_corpus(corpus)
    adapter = tmp_path / "adapter.py"
    adapter.write_text("# Synthetic adapter data; mock runner never executes this file.\n")
    entries = []
    for index, item in enumerate(prepared):
        mapping = tmp_path / f"mapping-{index}.json"
        mapping.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "name": "Synthetic unit experiment",
                    "adapter": str(adapter),
                    "purpose": "development_pilot",
                    "tasks": [
                        {
                            "task_id": item.case.task_id,
                            "repository": item.case.repository,
                            "base_commit": item.case.base_sha,
                            "issue_sha256": item.case.issue_sha256,
                            "supported": True,
                            "unsupported_reason": None,
                            "expected": {"value": 2},
                            "authorship": {
                                "scope": "development",
                                "timing": "pre_patch",
                                "basis": "Synthetic unit expectations, not research evidence.",
                            },
                        }
                    ],
                }
            )
        )
        record = asyncio.run(
            run_experiment(
                corpus, mapping, adapter, IMAGE, runner=RawRunner(), workspace_factory=workspace
            )
        )
        assert record["status"] == "completed"
        evidence = tmp_path / f"evidence-{index}.json"
        evidence.write_text(json.dumps(record))
        entries.append(
            {"evidence": str(evidence), "mapping": str(mapping), "adapter": str(adapter)}
        )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(entries))
    reviews = tmp_path / "reviews.json"
    reviews.write_text(json.dumps({"schema_version": 1, "reviews": []}))
    return corpus, manifest, reviews, entries


def edit_evidence(artifacts, change):
    entry = artifacts[3][0]
    from pathlib import Path

    path = Path(entry["evidence"])
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


def test_merges_disjoint_profiles_retains_all_cases_and_unknown_accuracy(artifacts):
    result = report(*artifacts[:3])
    assert len(result["rows"]) == 2
    assert {row["status"] for row in result["rows"]} == {"evaluated"}
    assert result["metrics"]["all_case_decision_coverage"] == 1
    assert result["metrics"]["reviewed_cases"] == 0
    assert result["metrics"]["precision"] is None
    assert result["metrics"]["recall"] is None
    assert result["metrics"]["case_analyses"] == {"false_positives": [], "false_negatives": []}
    assert result["by_profile_timing"]["pre_patch"]["total_cases"] == 2


def test_missing_group_retains_abstention(artifacts):
    corpus, manifest, reviews, entries = artifacts
    manifest.write_text(json.dumps(entries[:1]))
    result = report(corpus, manifest, reviews)
    assert len(result["rows"]) == 2
    assert result["rows"][1]["decisions"] == {"task_behavior": "abstain"}
    assert result["metrics"]["all_case_decision_coverage"] == 0.5


@pytest.mark.parametrize(
    "mutation",
    [
        "comparison",
        "decision",
        "response",
        "rawcontroller",
        "image",
        "corpus",
        "case",
        "workspace",
        "authorship",
        "frames",
    ],
)
def test_rejects_tampered_artifacts(artifacts, mutation):
    def change(record):
        row = record["rows"][0]
        if mutation == "comparison":
            row["comparison"]["scenarios"]["value"]["candidate_matches"] = False
        elif mutation == "decision":
            row["decisions"]["task_behavior"] = "reject"
        elif mutation == "response":
            row["observations"]["candidate"]["response"]["value"]["value"] = 10
        elif mutation == "rawcontroller":
            row["observations"]["candidate"]["raw_execution"]["controller_binding"]["sha256"] = (
                "f" * 64
            )
        elif mutation == "image":
            record["image_id"] = "python:latest"
        elif mutation == "corpus":
            record["corpus_sha256"] = "f" * 64
        elif mutation == "case":
            row["case_identity_sha256"] = "f" * 64
        elif mutation == "workspace":
            row["observations"]["candidate"]["workspace_sha256"] = None
        elif mutation == "authorship":
            row["profile_authorship"]["timing"] = "post_patch"
        else:
            raw = row["observations"]["candidate"]["raw_execution"]
            raw["stdout"] += "\n" + raw["stdout"]

    edit_evidence(artifacts, change)
    with pytest.raises(ValueError):
        report(*artifacts[:3])


def test_overlapping_supported_groups_rejected_even_if_same_decision(artifacts, tmp_path):
    corpus, manifest, reviews, entries = artifacts
    from pathlib import Path

    repeated = tmp_path / "repeated.json"
    repeated.write_bytes(Path(entries[0]["evidence"]).read_bytes())
    manifest.write_text(json.dumps(entries + [entries[0] | {"evidence": str(repeated)}]))
    with pytest.raises(ValueError, match="Overlapping"):
        report(corpus, manifest, reviews)


def test_no_frame_observations_remain_explicit_abstentions(artifacts):
    def change(record):
        row = record["rows"][0]
        for observed in row["observations"].values():
            observed["raw_execution"]["stdout"] = ""
            observed.update(
                response=None, decode_error="missing_response", usable=False, values=None
            )
        row.update(
            status="abstained",
            reason="observation_unavailable",
            decisions={"task_behavior": "abstain"},
            comparison={
                "decision": "abstain",
                "reason": "observation_unavailable",
                "scenarios": {},
            },
        )

    edit_evidence(artifacts, change)
    result = report(*artifacts[:3])
    assert result["rows"][0]["decisions"] == {"task_behavior": "abstain"}
    assert result["metrics"]["all_case_decision_coverage"] == 0.5


def test_actual_retained_human_review_only_changes_posthoc_accuracy(artifacts):
    corpus, manifest, reviews, _ = artifacts
    _, _, prepared = prepare_corpus(corpus)
    reviews.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reviews": [
                    {
                        "schema_version": 1,
                        "case_id": prepared[0].case.id,
                        "case_identity_sha256": prepared[0].identity_sha256,
                        "reviewer": "Synthetic unit reviewer",
                        "reviewed_at": "2026-10-07T20:00:00+00:00",
                        "verdict": "invalid",
                        "failure_categories": ["incomplete_fix"],
                        "rationale": "Synthetic unit review fixture, "
                        "not a real corpus correctness label.",
                        "attestation": "I personally reviewed this patch against the task.",
                    }
                ],
            }
        )
    )
    result = report(corpus, manifest, reviews)
    assert result["metrics"]["decided_confusion"] == {"TP": 0, "FP": 0, "FN": 1, "TN": 0}
    assert (
        result["metrics"]["case_analyses"]["false_negatives"][0]["case_id"] == prepared[0].case.id
    )
    assert result["rows"][1]["label"] == "unreviewed"


def unsupported_entry(artifacts, index, *, reason="requires unavailable network service"):
    from hashlib import sha256
    from pathlib import Path

    entry = artifacts[3][index]
    path = Path(entry["mapping"])
    specification = json.loads(path.read_text())
    profile = specification["tasks"][0]
    profile.update(supported=False, unsupported_reason=reason, expected={})
    profile["authorship"]["timing"] = "post_patch"
    path.write_text(json.dumps(specification))
    evidence = Path(entry["evidence"])
    record = json.loads(evidence.read_text())
    record["mapping_sha256"] = sha256(path.read_bytes()).hexdigest()
    row = record["rows"][index]
    row.update(
        status="abstained",
        reason=reason,
        observations={},
        profile_authorship=profile["authorship"],
        decisions={"task_behavior": "abstain"},
    )
    row.pop("comparison", None)
    evidence.write_text(json.dumps(record))
    return entry


def test_explicit_unsupported_profile_retains_reason_and_timing(artifacts):
    corpus, manifest, reviews, _ = artifacts
    entry = unsupported_entry(artifacts, 1)
    manifest.write_text(json.dumps([entry]))
    result = report(corpus, manifest, reviews)
    row = result["rows"][1]
    assert row["reason"] == "requires unavailable network service"
    assert row["profile_authorship"]["timing"] == "post_patch"
    assert row["unsupported_profile_reasons"][0]["reason"] == row["reason"]
    assert result["by_profile_timing"]["post_patch"]["total_cases"] == 1
    assert result["metrics"]["all_case_decision_coverage"] == 0


@pytest.mark.parametrize("unsupported_first", [True, False])
def test_unsupported_reasons_preserved_when_supported_owner_supersedes(
    artifacts, tmp_path, unsupported_first
):
    from copy import deepcopy
    from pathlib import Path

    original = deepcopy(artifacts[3][0])
    copied_mapping = tmp_path / "supported-mapping.json"
    copied_evidence = tmp_path / "supported-evidence.json"
    copied_mapping.write_bytes(Path(original["mapping"]).read_bytes())
    copied_evidence.write_bytes(Path(original["evidence"]).read_bytes())
    supported = original | {"mapping": str(copied_mapping), "evidence": str(copied_evidence)}
    unsupported = unsupported_entry(artifacts, 0)
    entries = [unsupported, supported] if unsupported_first else [supported, unsupported]
    artifacts[1].write_text(json.dumps(entries))
    result = report(*artifacts[:3])
    row = result["rows"][0]
    assert row["status"] == "evaluated"
    assert row["decisions"] == {"task_behavior": "accept"}
    assert row["profile_authorship"]["timing"] == "pre_patch"
    assert row["unsupported_profile_reasons"][0]["profile_authorship"]["timing"] == "post_patch"
    assert row["unsupported_profile_reasons"][0]["reason"] == "requires unavailable network service"
    assert result["metrics"]["all_case_decision_coverage"] == 0.5


def test_forged_unsupported_reason_rejected(artifacts):
    unsupported_entry(artifacts, 1)

    def change(record):
        record["rows"][1]["reason"] = "fabricated setup claim"

    from pathlib import Path

    path = Path(artifacts[3][1]["evidence"])
    record = json.loads(path.read_text())
    change(record)
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="Unsupported row reason"):
        report(*artifacts[:3])
