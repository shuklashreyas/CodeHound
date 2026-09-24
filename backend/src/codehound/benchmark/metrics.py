"""Label-aware scoring, including abstentions and conditional detection denominators."""

from codehound.execution.results import validate_report


def visible_decision(run):
    if not run or run.status != "completed" or run.evidence_error or run.exit_code not in (0, 1):
        return "abstain"
    try:
        validate_report(run.test_report, run.exit_code)
    except (ValueError, TypeError, KeyError):
        return "abstain"
    cases = run.test_report["tests"]
    if not cases or run.test_report["collection_errors"]:
        return "abstain"
    if any(case["outcome"] not in {"passed", "failed"} for case in cases):
        return "abstain"
    return "accept" if all(case["outcome"] == "passed" for case in cases) else "reject"


def independent_decision(assessment):
    verdict = assessment.get("verdict") if assessment else None
    if verdict == "candidate_improves":
        return "accept"
    if verdict in {"regression_detected", "incomplete"}:
        return "reject"
    return "abstain"


def score(rows, evaluator):
    counts = {
        label: {decision: 0 for decision in ("accept", "reject", "abstain")}
        for label in ("valid", "invalid", "unreviewed")
    }
    for row in rows:
        counts[row["label"]][row["decisions"][evaluator]] += 1
    valid, invalid = sum(counts["valid"].values()), sum(counts["invalid"].values())
    labeled = valid + invalid
    abstained = counts["valid"]["abstain"] + counts["invalid"]["abstain"]
    return {
        "total": len(rows),
        "labeled": labeled,
        "unreviewed": sum(counts["unreviewed"].values()),
        "confusion": counts,
        "invalid_detected": counts["invalid"]["reject"],
        "invalid_total": invalid,
        "valid_rejected": counts["valid"]["reject"],
        "valid_total": valid,
        "invalid_detection_rate": counts["invalid"]["reject"] / invalid if invalid else None,
        "false_positive_rate": counts["valid"]["reject"] / valid if valid else None,
        "decision_coverage": (labeled - abstained) / labeled if labeled else None,
    }


def score_group(rows):
    # Failed/skipped visible runs are excluded, not counted as visible success.
    subset = [row for row in rows if row["decisions"]["visible_only"] == "accept"]
    return {
        "all_candidates": {name: score(rows, name) for name in ("visible_only", "independent")},
        "visible_test_passing_candidates": {
            name: score(subset, name) for name in ("visible_only", "independent")
        },
        "definitions": {
            "invalid_detection_rate": "Rejected invalid / labeled invalid, including abstentions.",
            "false_positive_rate": "Rejected valid / all labeled valid; not rejection precision.",
            "decision_coverage": "Non-abstained labeled candidates / all labeled candidates.",
            "independent_accept": "Improvement with no configured failures; not full correctness.",
        },
    }


def summarize(rows):
    result = score_group(rows)
    result["by_split"] = {
        split: score_group([row for row in rows if row.get("split", "unspecified") == split])
        for split in sorted({row.get("split", "unspecified") for row in rows})
    }
    return result
