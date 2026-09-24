import base64
import json
from dataclasses import replace

import pytest

from codehound.execution.docker import ExecutionResult
from codehound.execution.results import PREFIX, compare_tests, decode_report


def execution(cases, *, exit_code=None):
    exit_code = (
        int(any(v in ("failed", "error") for v in cases.values()))
        if exit_code is None
        else exit_code
    )
    report = {
        "schema_version": 1,
        "exit_code": exit_code,
        "collected": list(cases),
        "tests": [
            {"nodeid": name, "outcome": outcome, "duration_seconds": 0.01, "message": ""}
            for name, outcome in cases.items()
        ],
        "collection_errors": [],
    }
    return ExecutionResult(
        "completed",
        exit_code,
        "",
        "",
        0.1,
        "sha256:" + "a" * 64,
        False,
        False,
        30,
        test_report=report,
    )


def frame(report):
    return PREFIX + "nonce:" + base64.b64encode(json.dumps(report).encode()).decode()


def test_mixed_fix_and_regression_is_not_hidden_by_equal_exit_codes():
    before = execution({"fixed": "failed", "broken": "passed", "still_bad": "failed"})
    after = execution({"fixed": "passed", "broken": "failed", "still_bad": "failed"})
    assert before.exit_code == after.exit_code == 1
    result = compare_tests(before, after)
    assert result["verdict"] == "regression_detected"
    assert [row["nodeid"] for row in result["regressions"]] == ["broken"]
    assert [row["nodeid"] for row in result["improvements"]] == ["fixed"]
    assert result["counts"]["unresolved"] == 1


def test_existing_failures_are_incomplete_not_regressions():
    before = execution({"test": "failed"})
    result = compare_tests(before, before)
    assert result["verdict"] == "incomplete"
    assert result["regressions"] == []


def test_deleted_test_cannot_turn_failure_into_improvement():
    result = compare_tests(
        execution({"good": "passed", "bad": "failed"}), execution({"good": "passed"})
    )
    assert result["verdict"] == "inconclusive"
    assert result["missing_tests"] == ["bad"]


@pytest.mark.parametrize("outcome", ["skipped", "xfailed", "xpassed", "error"])
def test_non_assertion_outcomes_are_not_claimed_as_improvements(outcome):
    result = compare_tests(execution({"test": "failed"}), execution({"test": outcome}))
    assert result["verdict"] == "inconclusive"
    assert result["improvements"] == []


def test_environment_change_invalidates_comparison():
    result = compare_tests(
        execution({"test": "passed"}),
        replace(execution({"test": "passed"}), evaluator_sha256="changed"),
    )
    assert result["verdict"] == "inconclusive"


def test_valid_frame_and_unrelated_output():
    report = execution({"test": "passed"}).test_report
    decoded, error = decode_report("hello\n" + frame(report) + "\n", "nonce", 0)
    assert error is None and decoded == report
    assert decode_report(frame(report), "different-nonce", 0) == (None, "missing_report")


def test_duplicate_and_truncated_frames_are_rejected():
    report = execution({"test": "passed"}).test_report
    assert decode_report(frame(report) + "\n" + frame(report), "nonce", 0)[1] == "duplicate_reports"
    assert decode_report(frame(report)[:-5], "nonce", 0)[1] == "invalid_report"


@pytest.mark.parametrize(
    "change",
    [
        {"exit_code": 1},
        {"collected": ["test", "missing"]},
        {"collected": ["test", "test"]},
        {"tests": []},
        {"collection_errors": ["error"]},
    ],
)
def test_report_must_match_inventory_and_container_exit(change):
    report = execution({"test": "passed"}).test_report | change
    assert decode_report(frame(report), "nonce", 0)[1] == "invalid_report"


def test_zero_exit_without_report_is_not_a_pass():
    result = compare_tests(
        execution({"test": "failed"}), replace(execution({"test": "passed"}), test_report=None)
    )
    assert result["verdict"] == "inconclusive"


def test_regression_remains_visible_alongside_skipped_checks():
    result = compare_tests(
        execution({"broken": "passed", "optional": "skipped"}),
        execution({"broken": "failed", "optional": "skipped"}),
    )
    assert result["verdict"] == "regression_detected"
    assert result["counts"]["unverified"] == 1
    assert result["reasons"]


def test_independent_existing_failures_are_not_called_regressions():
    from codehound.execution.results import summarize_comparisons

    suites = {
        "visible": {
            "test_comparison": compare_tests(
                execution({"example": "failed"}), execution({"example": "passed"})
            )
        },
        "hidden": {
            "test_comparison": compare_tests(
                execution({"edge": "failed"}), execution({"edge": "failed"})
            )
        },
    }
    result = summarize_comparisons(suites)
    assert result["verdict"] == "incomplete"
    assert result["signals"] == ["visible_improvement_with_unresolved_independent_tests"]


def test_valid_partial_external_evidence_preserves_a_known_regression():
    before = replace(
        execution({"broken": "passed", "unfinished": "passed"}),
        evidence_source="external_json_assertions",
    )
    after = replace(
        execution({"broken": "failed", "unfinished": "not_run"}, exit_code=2),
        status="timeout",
        evidence_source="external_json_assertions",
    )
    result = compare_tests(before, after)
    assert result["verdict"] == "regression_detected"
    assert result["regressions"] == [{"nodeid": "broken", "before": "passed", "after": "failed"}]
    assert result["counts"]["unverified"] == 1
    assert any("timeout" in reason for reason in result["reasons"])


def test_partial_improvement_is_retained_but_cannot_claim_success():
    before = replace(
        execution({"fixed": "failed", "unfinished": "passed"}),
        evidence_source="external_json_assertions",
    )
    after = replace(
        execution({"fixed": "passed", "unfinished": "not_run"}, exit_code=2),
        status="timeout",
        evidence_source="external_json_assertions",
    )
    result = compare_tests(before, after)
    assert result["verdict"] == "inconclusive"
    assert result["counts"]["improvements"] == 1
    for invalid in (
        replace(after, evaluator_sha256="changed"),
        replace(after, evidence_error="invalid_response"),
        replace(after, status="unavailable"),
    ):
        result = compare_tests(before, invalid)
        assert result["verdict"] == "inconclusive" and not result["improvements"]


def test_completed_collection_failure_preserves_valid_shared_outcomes():
    before = execution({"broken": "passed"})
    after = execution({"broken": "failed"}, exit_code=2)
    after.test_report["collection_errors"] = ["Additional test module could not be collected"]
    result = compare_tests(before, after)
    assert result["verdict"] == "regression_detected"
    assert "Test collection failed." in result["reasons"]
