"""Validate structured pytest evidence and compare identical test identities.

The report is emitted in-process: validation detects missing or inconsistent
reports, but is not proof against a deliberately compromised Python interpreter.
"""

import base64
import binascii
from collections import Counter

from codehound.execution.protocol import load_evidence

PREFIX = "CODEHOUND_TEST_REPORT_V1:"
MAX_TESTS = 10000
MAX_REPORT_BYTES = 750000
OUTCOMES = {"passed", "failed", "error", "skipped", "xfailed", "xpassed", "not_run"}


def decode_report(stdout: str, token: str, exit_code: int | None):
    marker = PREFIX + token + ":"
    frames = [line[len(marker) :] for line in stdout.splitlines() if line.startswith(marker)]
    if len(frames) != 1:
        return None, "missing_report" if not frames else "duplicate_reports"
    try:
        raw = base64.b64decode(frames[0], validate=True)
        if len(raw) > MAX_REPORT_BYTES:
            return None, "report_too_large"
        report = load_evidence(raw, limit=MAX_REPORT_BYTES)
        validate_report(report, exit_code)
    except (ValueError, TypeError, KeyError, RecursionError, binascii.Error):
        return None, "invalid_report"
    return report, None


def validate_report(report, exit_code):
    if (
        not isinstance(report, dict)
        or type(report.get("schema_version")) is not int
        or report["schema_version"] != 1
    ):
        raise ValueError("Unsupported report")
    if set(report) != {"schema_version", "exit_code", "collected", "tests", "collection_errors"}:
        raise ValueError("Unexpected report fields")
    if type(report.get("exit_code")) is not int or report["exit_code"] != exit_code:
        raise ValueError("Exit code differs")
    collected, tests = report.get("collected"), report.get("tests")
    if not isinstance(collected, list) or not isinstance(tests, list):
        raise ValueError("Missing inventory")
    if len(collected) > MAX_TESTS or len(tests) != len(collected):
        raise ValueError("Incomplete inventory")
    if any(not isinstance(name, str) or not 1 <= len(name) <= 2048 for name in collected):
        raise ValueError("Invalid test identity")
    if len(set(collected)) != len(collected):
        raise ValueError("Duplicate test identities")
    identities = []
    for case in tests:
        if not isinstance(case, dict) or case.get("outcome") not in OUTCOMES:
            raise ValueError("Invalid outcome")
        if set(case) != {"nodeid", "outcome", "duration_seconds", "message"}:
            raise ValueError("Unexpected test fields")
        if not isinstance(case.get("nodeid"), str):
            raise ValueError("Invalid test identity")
        identities.append(case["nodeid"])
        duration = case.get("duration_seconds")
        if type(duration) not in (int, float) or not 0 <= duration <= 3600:
            raise ValueError("Invalid duration")
        if not isinstance(case.get("message"), str) or len(case["message"]) > 2000:
            raise ValueError("Invalid failure message")
    if sorted(identities) != sorted(collected):
        raise ValueError("Test inventory differs")
    errors = report.get("collection_errors")
    if not isinstance(errors, list) or len(errors) > MAX_TESTS:
        raise ValueError("Invalid collection errors")
    if any(not isinstance(error, str) or len(error) > 2000 for error in errors):
        raise ValueError("Invalid collection error")
    outcomes = Counter(case["outcome"] for case in tests)
    if exit_code == 0 and (
        errors or outcomes["failed"] or outcomes["error"] or outcomes["not_run"]
    ):
        raise ValueError("Successful exit contradicts outcomes")
    if exit_code == 1 and not (outcomes["failed"] or outcomes["error"]):
        raise ValueError("Failure exit without failures")


def compare_tests(baseline, candidate):
    """Keep regression and improvement evidence even when both processes fail."""
    result = {
        "verdict": "inconclusive",
        "improvements": [],
        "regressions": [],
        "unresolved": [],
        "unchanged_passes": [],
        "unverified": [],
        "missing_tests": [],
        "added_tests": [],
        "reasons": [],
        "counts": {},
    }
    if (baseline.image_id, baseline.evaluator_sha256, baseline.evidence_source) != (
        candidate.image_id,
        candidate.evaluator_sha256,
        candidate.evidence_source,
    ):
        result["reasons"].append("Execution image or evaluator changed between revisions.")
    for label, run in (("baseline", baseline), ("candidate", candidate)):
        if run.status != "completed" or run.exit_code not in (0, 1):
            result["reasons"].append(f"{label}: {run.status}, exit {run.exit_code}")
        if run.evidence_error or run.test_report is None:
            result["reasons"].append(f"{label}: {run.evidence_error or 'missing_report'}")
        else:
            try:
                validate_report(run.test_report, run.exit_code)
            except (ValueError, TypeError, KeyError):
                result["reasons"].append(f"{label}: invalid_report")
    if result["reasons"]:
        return result
    old = {case["nodeid"]: case for case in baseline.test_report["tests"]}
    new = {case["nodeid"]: case for case in candidate.test_report["tests"]}
    result["missing_tests"] = sorted(old.keys() - new.keys())
    result["added_tests"] = sorted(new.keys() - old.keys())
    if not old or not new:
        result["reasons"].append("No comparable tests were collected.")
    if old.keys() != new.keys():
        result["reasons"].append("Test collection changed between revisions.")
    if baseline.test_report["collection_errors"] or candidate.test_report["collection_errors"]:
        result["reasons"].append("Test collection failed.")
    categories = {
        ("failed", "passed"): "improvements",
        ("passed", "failed"): "regressions",
        ("failed", "failed"): "unresolved",
        ("passed", "passed"): "unchanged_passes",
    }
    for nodeid in sorted(old.keys() & new.keys()):
        before, after = old[nodeid]["outcome"], new[nodeid]["outcome"]
        category = categories.get((before, after), "unverified")
        result[category].append({"nodeid": nodeid, "before": before, "after": after})
    fields = (*categories.values(), "unverified", "missing_tests", "added_tests")
    result["counts"] = {field: len(result[field]) for field in fields}
    if result["unverified"]:
        result["reasons"].append("Some tests were skipped, expected failures, or execution errors.")
    if result["regressions"]:
        result["verdict"] = "regression_detected"
    elif result["reasons"]:
        return result
    elif result["unresolved"]:
        result["verdict"] = "incomplete"
    elif result["improvements"]:
        result["verdict"] = "candidate_improves"
    else:
        result["verdict"] = "no_behavior_change_observed"
    return result


def summarize_comparisons(suites):
    """An improvement in visible tests cannot conceal independent failures."""
    comparisons = {name: suite["test_comparison"] for name, suite in suites.items()}
    verdicts = {item["verdict"] for item in comparisons.values()}
    priority = (
        "regression_detected",
        "inconclusive",
        "incomplete",
        "candidate_improves",
        "no_behavior_change_observed",
    )
    verdict = next((value for value in priority if value in verdicts), "inconclusive")
    signals = []
    visible, hidden = comparisons.get("visible"), comparisons.get("hidden")
    if visible and hidden and visible["improvements"]:
        if hidden["regressions"]:
            signals.append("visible_improvement_with_independent_regression")
        elif hidden["unresolved"]:
            signals.append("visible_improvement_with_unresolved_independent_tests")
    return {"verdict": verdict, "signals": signals, "confidence": None}
