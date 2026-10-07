import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_results import execution

from codehound.benchmark.manifest import Manifest, prepare_manifest, retained_path
from codehound.benchmark.metrics import independent_decision, summarize, visible_decision
from codehound.benchmark.run import run_benchmark

IMAGE = "sha256:" + "a" * 64


def make_dataset(root):
    for name in ("baseline", "correct", "bad"):
        path = root / name
        path.mkdir()
        (path / "candidate.py").write_text("def answer(value): return value\n")
    for name in ("visible", "hidden"):
        (root / f"{name}.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "module": "candidate",
                    "function": "answer",
                    "cases": [{"id": "one", "args": [5], "expect": {"kind": "value", "value": 10}}],
                }
            )
        )
    manifest = {
        "name": "Unit experiment",
        "dataset_kind": "synthetic_demo",
        "provenance": "Hand-authored unit fixture; not a research benchmark.",
        "tasks": [
            {
                "id": "task",
                "family": "family",
                "split": "demo",
                "issue": "Double the supplied integer value.",
                "baseline": "baseline",
                "visible_profile": "visible.json",
                "independent_profile": "hidden.json",
                "candidates": [
                    {
                        "id": name,
                        "workspace": name,
                        "label": label,
                        "label_reason": "A specification-based fixture label.",
                    }
                    for name, label in (("correct", "valid"), ("bad", "invalid"))
                ],
            }
        ],
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path, manifest


class Runner:
    async def run(self, workspace, profile):
        passed = (
            workspace.name == "correct" or workspace.name == "bad" and profile.name == "visible"
        )
        return replace(
            execution({profile.name: "passed" if passed else "failed"}),
            evaluator_sha256=profile.sha256,
        )


def test_experiment_compares_visible_and_independent_without_labels_in_execution(tmp_path):
    path, manifest = make_dataset(tmp_path)
    result = asyncio.run(run_benchmark(path, IMAGE, runner=Runner()))
    assert result["status"] == "completed"
    metrics = result["metrics"]["visible_test_passing_candidates"]
    assert metrics["visible_only"]["invalid_detected"] == 0
    assert (
        metrics["independent"]["invalid_detected"] == metrics["independent"]["invalid_total"] == 1
    )
    assert metrics["independent"]["valid_rejected"] == 0
    assert [row["decisions"]["independent"] for row in result["rows"]] == ["accept", "reject"]
    # Changing labels changes scoring, not any evaluator decision.
    for candidate in manifest["tasks"][0]["candidates"]:
        candidate["label"] = "unreviewed"
    path.write_text(json.dumps(manifest))
    relabeled = asyncio.run(run_benchmark(path, IMAGE, runner=Runner()))
    assert [row["decisions"] for row in result["rows"]] == [
        row["decisions"] for row in relabeled["rows"]
    ]
    assert relabeled["metrics"]["all_candidates"]["independent"]["invalid_detection_rate"] is None


def test_false_positives_and_abstentions_keep_explicit_denominators():
    rows = [
        {"label": label, "decisions": {"visible_only": "accept", "independent": decision}}
        for label in ("valid", "invalid", "unreviewed")
        for decision in ("accept", "reject", "abstain")
    ]
    scored = summarize(rows)["all_candidates"]["independent"]
    assert scored["invalid_total"] == scored["valid_total"] == 3
    assert scored["invalid_detection_rate"] == scored["false_positive_rate"] == 1 / 3
    assert scored["decision_coverage"] == 2 / 3
    assert scored["unreviewed"] == 3


def test_visible_success_excludes_skips_and_inconsistent_reports():
    assert visible_decision(execution({"case": "skipped"})) == "abstain"
    assert visible_decision(replace(execution({"case": "passed"}), status="timeout")) == "abstain"
    assert visible_decision(replace(execution({"case": "passed"}), test_report=None)) == "abstain"
    assert independent_decision({"verdict": "no_behavior_change_observed"}) == "abstain"
    rows = [{"label": "invalid", "decisions": {"visible_only": "abstain", "independent": "reject"}}]
    assert summarize(rows)["visible_test_passing_candidates"]["independent"]["invalid_total"] == 0


def test_changed_dataset_invalidates_all_decisions(tmp_path):
    path, _ = make_dataset(tmp_path)

    class Mutating(Runner):
        async def run(self, workspace, profile):
            if workspace.name == "bad":
                (workspace / "candidate.py").write_text("changed after its identity was captured")
            return await super().run(workspace, profile)

    result = asyncio.run(run_benchmark(path, IMAGE, runner=Mutating()))
    assert result["status"] == "invalidated"
    assert all(row["decisions"]["independent"] == "abstain" for row in result["rows"])
    assert result["metrics"]["all_candidates"]["independent"]["decision_coverage"] == 0


def test_deadline_preserves_partial_evidence_and_abstention(tmp_path):
    path, _ = make_dataset(tmp_path)

    class Slow(Runner):
        async def run(self, workspace, profile):
            if workspace.name == "correct" and profile.name == "hidden":
                await asyncio.Future()
            return await super().run(workspace, profile)

    result = asyncio.run(run_benchmark(path, IMAGE, runner=Slow(), max_seconds=1))
    assert result["status"] == "timeout"
    assert result["rows"][0]["status"] == "interrupted"
    assert result["rows"][0]["decisions"] == {"visible_only": "accept", "independent": "abstain"}
    assert result["rows"][0]["suites"]["visible"]["candidate"]["test_report"]
    assert result["rows"][1]["status"] == "not_run"


def test_manifest_rejects_family_leakage_duplicate_ids_and_untrusted_paths(tmp_path):
    _, manifest = make_dataset(tmp_path)
    manifest["dataset_kind"] = "human_reviewed"
    manifest["tasks"][0]["split"] = "train"
    manifest["tasks"].append(manifest["tasks"][0] | {"id": "other", "split": "evaluation"})
    with pytest.raises(ValidationError, match="same split"):
        Manifest.model_validate(manifest)
    with pytest.raises(ValueError):
        retained_path(tmp_path, "../outside")
    (tmp_path / "linked").symlink_to(tmp_path / "baseline", target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        retained_path(tmp_path, "linked")


def test_public_corpus_prepares_without_executing_candidate_code():
    path = Path(__file__).parents[1] / "fixtures" / "benchmark.json"
    manifest, checksum, prepared = prepare_manifest(path)
    assert manifest.dataset_kind == "synthetic_demo" and len(checksum) == 64
    assert len(prepared) == 3
    assert sum(len(task.candidates) for task in manifest.tasks) == 12


def test_cancellation_saves_partial_evidence_then_propagates(tmp_path):
    path, _ = make_dataset(tmp_path)
    snapshots = []

    async def experiment():
        entered = asyncio.Event()

        class Blocking(Runner):
            async def run(self, workspace, profile):
                if workspace.name == "correct":
                    entered.set()
                    await asyncio.Future()
                return await super().run(workspace, profile)

        task = asyncio.create_task(
            run_benchmark(
                path,
                IMAGE,
                runner=Blocking(),
                checkpoint=lambda record: snapshots.append(json.loads(json.dumps(record))),
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(experiment())
    assert snapshots[-1]["status"] == "cancelled"
    assert snapshots[-1]["tasks"][0]["baseline"]
    assert snapshots[-1]["metrics"]["all_candidates"]["independent"]["decision_coverage"] == 0


def test_atomic_checkpoints_never_overwrite_existing_experiment(tmp_path):
    from codehound.benchmark.run import evidence_writer

    path = tmp_path / "result.json"
    first = evidence_writer(path)
    first({"status": "running"})
    with pytest.raises(FileExistsError):
        evidence_writer(path)({"status": "wrong run"})
    assert json.loads(path.read_text())["status"] == "running"
    first({"status": "completed"})
    assert json.loads(path.read_text())["status"] == "completed"
    assert not list(tmp_path.glob(".codehound-benchmark-*"))


def test_evaluation_split_is_not_blended_with_training_results():
    rows = [
        {
            "split": "train",
            "label": "invalid",
            "decisions": {"visible_only": "accept", "independent": "reject"},
        },
        {
            "split": "evaluation",
            "label": "invalid",
            "decisions": {"visible_only": "accept", "independent": "accept"},
        },
    ]
    metrics = summarize(rows)
    assert metrics["all_candidates"]["independent"]["invalid_detection_rate"] == 0.5
    assert (
        metrics["by_split"]["evaluation"]["all_candidates"]["independent"]["invalid_detection_rate"]
        == 0
    )
    assert (
        metrics["by_split"]["train"]["all_candidates"]["independent"]["invalid_detection_rate"] == 1
    )


def test_docker_public_corpus_detects_overfits_and_regressions():
    import os

    image = os.getenv("CODEHOUND_TEST_IMAGE_ID")
    if not image:
        pytest.skip("Trusted Docker image not configured")
    path = Path(__file__).parents[1] / "fixtures" / "benchmark.json"
    result = asyncio.run(run_benchmark(path, image))
    assert result["status"] == "completed"
    subset = result["metrics"]["visible_test_passing_candidates"]
    assert subset["visible_only"]["invalid_total"] == 6
    assert subset["visible_only"]["invalid_detected"] == 0
    assert subset["independent"]["invalid_detected"] == 6
    assert subset["independent"]["valid_total"] == 3
    assert subset["independent"]["valid_rejected"] == 0
    assert all(
        row["assessment"]["verdict"] == "regression_detected"
        for row in result["rows"]
        if row["candidate_id"] == "regressive"
    )
