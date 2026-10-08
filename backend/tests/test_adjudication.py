"""Reference labels must remain blinded, identity-bound and provisional."""

import copy
import hashlib

import pytest

from codehound.benchmark.adjudication import (
    adjudicate,
    canonical_sha256,
    reference_metrics,
    validate_bundle,
)


def material(identifier, kind, content, **extra):
    return {
        "id": identifier,
        "kind": kind,
        "content": content,
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
        "size_bytes": len(content.encode()),
        **extra,
    }


def bundle(number=1, *, runtime=True):
    result = {
        "schema_version": 1,
        "case_id": f"case-{number}",
        "patch_id": f"task-{number}",
        "case_identity_sha256": hashlib.sha256(f"case-{number}".encode()).hexdigest(),
        "materials": [
            material("issue", "issue", "Handle empty collections."),
            material("patch", "patch", "return items or []"),
            material("context", "code", "def collect(items): ..."),
        ],
        "neutral_evidence": [
            material("runtime-1", "runtime", "collect([]) -> []", status="completed"),
        ]
        if runtime
        else [],
    }
    result["material_sha256"] = canonical_sha256(result)
    return result


def review(delivered, slot="a", *, verdict="Correct", confidence="High", failure=None):
    has_runtime = bool(delivered["neutral_evidence"])
    inspected = [item["id"] for item in delivered["materials"] + delivered["neutral_evidence"]]
    return {
        "schema_version": 1,
        **{
            key: delivered[key]
            for key in (
                "case_id",
                "patch_id",
                "case_identity_sha256",
                "material_sha256",
            )
        },
        "reviewer_id": f"reviewer-{slot}",
        "model_id": f"model-{slot}",
        "context_id": f"context-{slot}-{delivered['case_id']}",
        "verdict": verdict,
        "confidence": confidence,
        "evidence": [
            {
                "requirement": "Empty collection supported",
                "claim": "Observed empty output",
                "evidence_ids": ["runtime-1"] if has_runtime else ["context"],
                "failure_id": failure,
            }
        ],
        "verified_material_ids": inspected,
        "actually_verified": [
            "Read delivered issue, diff, surrounding function and supplied runtime."
        ],
        "uncertainty": [],
    }


def cohort(count=5, *, verdict="Correct", runtime=True, failure=None):
    bundles = [bundle(index, runtime=runtime) for index in range(count)]
    a = [review(item, "a", verdict=verdict, failure=failure) for item in bundles]
    b = [review(item, "b", verdict=verdict, failure=failure) for item in bundles]
    return bundles, a, b


def test_correct_agreement_has_provisional_labels_and_pending_random_audit():
    bundles, a, b = cohort()
    result = adjudicate(bundles, a, b, seed="frozen-seed")
    assert result["human_ground_truth"] is False
    assert result["summary"] == {
        "cases": 5,
        "raw_agreements": 5,
        "disagreements": 0,
        "provisional_labels": 5,
        "human_review_required": 1,
        "reference_scoring_eligible": 4,
    }
    assert all(row["label_provenance"] == "provisional_ai_consensus" for row in result["rows"])
    sampled = [row for row in result["rows"] if row["agreement_audit_selected"]]
    assert len(sampled) == 1
    assert sampled[0]["reference_label"] == "correct"
    assert not sampled[0]["reference_scoring_eligible"]
    assert result == adjudicate(list(reversed(bundles)), list(reversed(a)), b, seed="frozen-seed")


def test_incorrect_requires_same_runtime_demonstrated_failure():
    bundles, a, b = cohort(verdict="Incorrect", failure="Drops None input")
    b[0]["evidence"][0]["failure_id"] = "  DROPS None  input "
    result = adjudicate(bundles, a, b)
    assert result["summary"]["provisional_labels"] == 5
    b[0]["evidence"][0]["failure_id"] = "Wrong output ordering"
    result = adjudicate(bundles, a, b)
    assert result["rows"][0]["reference_label"] is None
    assert "no_shared_demonstrated_failure" in result["rows"][0]["human_review_reasons"]


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda a, b: b.update(verdict="Incorrect"), "reviewer_disagreement"),
        (lambda a, b: b.update(verdict="Unclear"), "unclear_review"),
        (lambda a, b: b.update(confidence="Medium"), "not_high_confidence"),
        (lambda a, b: a.update(confidence="Low"), "not_high_confidence"),
        (
            lambda a, b: a["evidence"][0].update(evidence_ids=["context"]),
            "no_concrete_behavioral_evidence",
        ),
    ],
)
def test_weak_agreements_require_human(mutation, reason):
    bundles, a, b = cohort(count=1)
    mutation(a[0], b[0])
    result = adjudicate(bundles, a, b)
    row = result["rows"][0]
    assert row["human_review_required"]
    assert not row["reference_scoring_eligible"]
    assert reason in row["human_review_reasons"]


def test_no_runtime_all_abstain_from_reference_labels():
    result = adjudicate(*cohort(count=50, runtime=False))
    assert result["summary"]["human_review_required"] == 50
    assert result["summary"]["provisional_labels"] == 0


@pytest.mark.parametrize("missing", ["issue", "patch", "context"])
def test_runtime_agreement_without_core_material_inspection_requires_human(missing):
    bundles, a, b = cohort(count=1)
    a[0]["verified_material_ids"].remove(missing)
    result = adjudicate(bundles, a, b)
    row = result["rows"][0]
    assert row["reference_label"] is None
    assert "not_all_core_materials_inspected" in row["human_review_reasons"]


def test_unclear_reviewer_may_truthfully_report_no_material_inspection():
    bundles, a, b = cohort(count=1)
    a[0].update(verdict="Unclear", verified_material_ids=[], evidence=[])
    a[0]["actually_verified"] = ["No material inspected; unable to assess."]
    result = adjudicate(bundles, a, b)
    assert result["rows"][0]["human_review_required"]
    assert "not_all_core_materials_inspected" in result["rows"][0]["human_review_reasons"]


def test_unavailable_runtime_never_produces_label():
    bundles, a, b = cohort(count=1)
    bundles[0]["neutral_evidence"][0]["status"] = "unavailable"
    bundles[0]["material_sha256"] = canonical_sha256(bundles[0])
    for slot in (a, b):
        slot[0]["material_sha256"] = bundles[0]["material_sha256"]
    result = adjudicate(bundles, a, b)
    assert result["summary"]["provisional_labels"] == 0


@pytest.mark.parametrize("field", ["reviewer_id", "model_id", "context_id"])
def test_review_independence_required(field):
    bundles, a, b = cohort(count=1)
    b[0][field] = a[0][field]
    with pytest.raises(ValueError, match="contexts|fresh context"):
        adjudicate(bundles, a, b)


@pytest.mark.parametrize("other_slot", ["a", "b"])
def test_batch_context_reuse_rejected_across_the_whole_cohort(other_slot):
    bundles, a, b = cohort(count=2)
    slot = a if other_slot == "a" else b
    slot[1]["context_id"] = a[0]["context_id"]
    with pytest.raises(ValueError, match="fresh context across both cohorts"):
        adjudicate(bundles, a, b)


@pytest.mark.parametrize("field", ["case_identity_sha256", "material_sha256", "patch_id"])
def test_stale_identity_or_different_review_material_rejected(field):
    bundles, a, b = cohort(count=1)
    b[0][field] = "changed"
    with pytest.raises(ValueError, match="identity/material"):
        adjudicate(bundles, a, b)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["evidence"][0].update(evidence_ids=["fabricated-test"]),
        lambda value: value["verified_material_ids"].append("outside-file"),
        lambda value: value["verified_material_ids"].remove("runtime-1"),
    ],
)
def test_fabricated_or_uninspected_evidence_rejected(mutation):
    bundles, a, b = cohort(count=1)
    mutation(a[0])
    with pytest.raises(ValueError):
        adjudicate(bundles, a, b)


def test_duplicate_and_incomplete_reviews_rejected():
    bundles, a, b = cohort(count=2)
    with pytest.raises(ValueError, match="duplicate cases"):
        adjudicate(bundles, [a[0], a[0]], b)
    with pytest.raises(ValueError, match="complete material inventory"):
        adjudicate(bundles, a[:1], b)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["materials"][0].update(content="changed issue"),
        lambda value: value["materials"][0].update(size_bytes=123),
        lambda value: value["materials"].append(copy.deepcopy(value["materials"][0])),
        lambda value: value.update(codehound_verdict="accept"),
        lambda value: value["materials"][0].pop("content"),
    ],
)
def test_changed_or_unsafe_bundle_rejected(mutation):
    value = bundle()
    mutation(value)
    value["material_sha256"] = canonical_sha256(value)
    with pytest.raises(ValueError):
        validate_bundle(value)


@pytest.mark.parametrize("rate", [0, 0.19, 1.1, float("nan"), True])
def test_sample_rate_must_preserve_twenty_percent_audit(rate):
    with pytest.raises(ValueError):
        adjudicate(*cohort(count=1), sample_rate=rate)


def report_for(result, predictions):
    return {
        "kind": "validated_task_experiment_report",
        "corpus_sha256": "a" * 64,
        "rows": [
            {
                "case_id": row["patch_id"],
                "case_identity_sha256": row["case_identity_sha256"],
                "decisions": {"task_behavior": prediction},
            }
            for row, prediction in zip(result["rows"], predictions)
        ],
    }


def test_conditional_metrics_do_not_conflate_accuracy_and_coverage():
    bundles, a, b = cohort(count=6)
    for index in (0, 1, 4):
        for slot in (a, b):
            slot[index]["verdict"] = "Incorrect"
            slot[index]["evidence"][0]["failure_id"] = "Observed empty-input failure"
    result = adjudicate(bundles, a, b)
    # Exclude the sampled cases and assign one of each confusion category plus abstention.
    pending = [row for row in result["rows"] if not row["reference_scoring_eligible"]]
    assert len(pending) == 2
    predictions = []
    incorrect, correct = 0, 0
    expected = dict.fromkeys(("tp", "fp", "fn", "tn"), 0)
    for row in result["rows"]:
        if not row["reference_scoring_eligible"]:
            predictions.append("accept")
        elif row["reference_label"] == "incorrect":
            prediction = "reject" if incorrect == 0 else "accept"
            predictions.append(prediction)
            expected["tp" if incorrect == 0 else "fn"] += 1
            incorrect += 1
        else:
            prediction = "reject" if correct == 0 else "abstain"
            predictions.append(prediction)
            expected["fp"] += correct == 0
            correct += 1
    metrics = reference_metrics(result, report_for(result, predictions))
    assert metrics["confusion"] == expected
    assert metrics["scoring_eligible_reference_labels"] == 4
    assert metrics["human_ground_truth"] is False
    assert "reviewed" not in metrics
    assert metrics["reference_label_coverage"] == 4 / 6
    assert metrics["all_case_coverage"] == sum(value != "abstain" for value in predictions) / 6


def test_no_labels_means_null_rates_but_execution_coverage_is_known():
    result = adjudicate(*cohort(count=2, runtime=False))
    metrics = reference_metrics(result, report_for(result, ["accept", "abstain"]))
    assert metrics["all_case_coverage"] == 0.5
    for field in (
        "precision",
        "recall",
        "false_positive_rate",
        "false_negative_rate",
        "reference_coverage",
    ):
        assert metrics[field] is None


def test_join_rejects_changed_patch_identity():
    result = adjudicate(*cohort(count=2))
    report = report_for(result, ["accept", "reject"])
    report["rows"][0]["case_identity_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="different patch identity"):
        reference_metrics(result, report)


def test_metrics_join_blinded_aliases_to_original_patch_ids():
    bundles, a, b = cohort(count=5, verdict="Incorrect", failure="Empty-input regression")
    result = adjudicate(bundles, a, b)
    metrics = reference_metrics(result, report_for(result, ["accept"] * 5))
    assert metrics["confusion"]["fn"] == 4
    for error in metrics["error_analysis"]["false_negatives"]:
        assert error["case_id"].startswith("case-")
        assert error["patch_id"].startswith("task-")


def test_duplicate_patch_ids_cannot_count_multiple_reference_aliases():
    result = adjudicate(*cohort(count=2))
    result["rows"][1]["patch_id"] = result["rows"][0]["patch_id"]
    with pytest.raises(ValueError, match="duplicate an original patch identity"):
        reference_metrics(result, report_for(result, ["accept", "reject"]))


def test_unreviewed_agreement_sample_cannot_be_forced_into_scoring():
    result = adjudicate(*cohort(count=1))
    assert result["rows"][0]["agreement_audit_selected"]
    result["rows"][0]["reference_scoring_eligible"] = True
    with pytest.raises(ValueError, match="cannot be scoring eligible"):
        reference_metrics(result, report_for(result, ["accept"]))


def test_reference_label_cannot_disagree_with_stored_blinded_reviews():
    result = adjudicate(*cohort(count=5))
    result["rows"][0]["reference_label"] = "incorrect"
    with pytest.raises(ValueError, match="differs from its bound"):
        reference_metrics(result, report_for(result, ["accept"] * 5))
