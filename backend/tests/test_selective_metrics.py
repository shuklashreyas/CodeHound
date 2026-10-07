"""Metric fixtures are synthetic arithmetic examples, never human benchmark labels."""

import json

import pytest

from codehound.benchmark.corpus_metrics import score


def row(case_id, label, decision, *, eligible=True, status="agreed", evaluator="task_behavior"):
    return {
        "case_id": case_id,
        "case_identity_sha256": "a" * 64,
        "label": label,
        "accuracy_eligible": eligible,
        "review_status": status,
        "decisions": {evaluator: decision},
        "reviews": [
            {
                "reviewer": "Synthetic test fixture reviewer",
                "rationale": "Synthetic fixture rationale for arithmetic verification.",
                "failure_categories": ["incomplete_fix"] if label == "invalid" else [],
            }
        ],
        "comparison": {
            "header-default-none": {
                "expected": {"Accept": "ok"},
                "candidate_observed": {"Accept": None},
            }
        },
        "unresolved": ["header-default-none"],
    }


def test_conditional_accuracy_has_decided_reviewed_denominators_and_separate_coverage():
    rows = [
        row("tp1", "invalid", "reject"),
        row("tp2", "invalid", "reject"),
        row("fp", "valid", "reject"),
        row("fn", "invalid", "accept"),
        *[row(f"tn{i}", "valid", "accept") for i in range(3)],
        *[row(f"valid-abstain{i}", "valid", "abstain") for i in range(2)],
        *[row(f"invalid-abstain{i}", "invalid", "abstain") for i in range(4)],
        row("unknown", "unreviewed", "reject", eligible=False, status="unreviewed"),
        row("conflict", "valid", "accept", status="conflicting"),
        row("uncertain", "unreviewed", "abstain", status="uncertain"),
    ]
    result = score(rows, "task_behavior")
    assert result["metric_definition_version"] == 2
    assert result["positive_class"] == "invalid_patch"
    assert result["decided_confusion"] == {"TP": 2, "FP": 1, "FN": 1, "TN": 3}
    assert result["precision"] == pytest.approx(2 / 3)
    assert result["recall"] == pytest.approx(2 / 3)
    assert result["false_positive_rate"] == pytest.approx(1 / 4)
    assert result["false_negative_rate"] == pytest.approx(1 / 3)
    assert result["valid_denominator"] == 6
    assert result["invalid_denominator"] == 7
    assert result["reviewed_cases"] == 13
    assert result["decided_reviewed_cases"] == 7
    assert result["excluded_unknown_or_ambiguous"] == 3
    assert result["all_case_decision_coverage"] == pytest.approx(9 / 16)
    assert result["all_case_abstention_rate"] == pytest.approx(7 / 16)
    assert result["reviewed_decision_coverage"] == pytest.approx(7 / 13)
    assert result["reviewed_abstention_rate"] == pytest.approx(6 / 13)
    assert result["population_yield"]["bad_patch_detection_rate"] == pytest.approx(2 / 7)
    assert result["population_yield"]["false_positive_fraction"] == pytest.approx(1 / 6)
    assert result["bad_patch_detection_rate"] == pytest.approx(2 / 7)


def test_case_analyses_only_name_reviewed_false_positives_and_false_negatives():
    rows = [
        row("human-valid-rejected", "valid", "reject"),
        row("human-invalid-accepted", "invalid", "accept"),
        row("human-invalid-rejected", "invalid", "reject"),
        row("unreviewed-rejected", "unreviewed", "reject", eligible=False, status="unreviewed"),
        row("conflicting-accepted", "invalid", "accept", status="conflicting"),
    ]
    result = score(rows, "task_behavior")
    (fp,) = result["case_analyses"]["false_positives"]
    (fn,) = result["case_analyses"]["false_negatives"]
    assert fp["case_id"] == "human-valid-rejected"
    assert fp["human_label"] == "valid"
    assert fp["failure_categories"] == []
    assert fn["case_id"] == "human-invalid-accepted"
    assert fn["failure_categories"] == ["incomplete_fix"]
    assert fn["evaluator_evidence"]["unresolved"] == ["header-default-none"]
    assert fn["human_review_explanations"][0]["rationale"].startswith("Synthetic fixture")
    assert {item["case_id"] for item in result["unreviewed_review_candidates"]} == {
        "unreviewed-rejected",
        "conflicting-accepted",
    }
    assert all("human_label" not in item for item in result["unreviewed_review_candidates"])


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [row("unknown", "unreviewed", "abstain", eligible=False)],
        [row("valid", "valid", "abstain"), row("invalid", "invalid", "abstain")],
    ],
)
def test_no_decided_reviewed_cases_produces_null_accuracy_not_zero(rows):
    result = score(rows, "task_behavior")
    assert result["decided_reviewed_cases"] == 0
    for name in ("precision", "recall", "false_positive_rate", "false_negative_rate"):
        assert result[name] is None
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    if rows:
        assert result["all_case_abstention_rate"] == 1
        assert result["all_case_decision_coverage"] == 0
    else:
        assert result["all_case_abstention_rate"] is None
        assert result["all_case_decision_coverage"] is None


def test_unsupported_reviewed_patch_remains_abstention_in_coverage_and_yield():
    rows = [
        row("supported", "invalid", "reject"),
        row("unsupported", "invalid", "abstain") | {"status": "unsupported"},
        *[
            row(f"unknown{i}", "unreviewed", "abstain", eligible=False, status="unreviewed")
            for i in range(48)
        ],
    ]
    result = score(rows, "task_behavior")
    assert result["total_cases"] == 50
    assert result["recall"] == 1
    assert result["population_yield"]["bad_patch_detection_rate"] == 0.5
    assert result["all_case_decision_coverage"] == 0.02
    assert result["all_case_abstention_rate"] == 0.98
    assert result["reviewed_decision_coverage"] == 0.5
    assert result["invalid_abstentions"] == 1


def test_unknown_or_ambiguous_labels_never_enter_accuracy_even_with_eligible_flag():
    rows = [
        row("unknown", "unreviewed", "reject"),
        row("uncertain", "invalid", "accept", status="uncertain"),
        row("conflict", "valid", "reject", status="conflicting"),
    ]
    result = score(rows, "task_behavior")
    assert result["reviewed_cases"] == 0
    assert result["decided_confusion"] == {"TP": 0, "FP": 0, "FN": 0, "TN": 0}
    assert result["case_analyses"] == {"false_positives": [], "false_negatives": []}
    assert len(result["unreviewed_review_candidates"]) == 3


@pytest.mark.parametrize(
    "evaluator",
    [
        "independent",
        "static_only",
        "repository_tests_only",
        "visible_only",
        "task_behavior",
        "future_adapter",
    ],
)
def test_score_supports_generic_evaluator_names(evaluator):
    result = score([row("one", "invalid", "reject", evaluator=evaluator)], evaluator)
    assert result["precision"] == result["recall"] == 1
    assert result["false_positive_rate"] is None
    assert result["false_negative_rate"] == 0


@pytest.mark.parametrize("decision", [None, "not_run", "unsupported", "unknown", True])
def test_unknown_decision_is_rejected_instead_of_forced_to_abstention(decision):
    with pytest.raises(ValueError, match="accept, reject or abstain"):
        score([row("one", "invalid", decision)], "task_behavior")
