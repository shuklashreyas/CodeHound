"""Post-hoc, identity-bound scoring; missing human reviews never become labels."""

import argparse
import hashlib
import re
from dataclasses import fields
from pathlib import Path

from codehound.benchmark.corpus import prepare_corpus
from codehound.benchmark.corpus_run import (
    CONTROLLERS,
    EVALUATORS,
    prepare_mapping,
    regular_bytes,
    static_decision,
)
from codehound.benchmark.metrics import independent_decision, visible_decision
from codehound.benchmark.review import join_reviews, load_reviews
from codehound.execution.docker import CONTROLLERS as DOCKER_CONTROLLERS
from codehound.execution.docker import ExecutionResult
from codehound.execution.independent import CONTROLLERS as INDEPENDENT_CONTROLLERS
from codehound.execution.independent import judge
from codehound.execution.protocol import load_evidence
from codehound.execution.provenance import bind_source, controller_binding
from codehound.execution.repository_tests import CONTROLLERS as REPOSITORY_CONTROLLERS
from codehound.execution.repository_tests import SOURCE as REPOSITORY_SOURCE
from codehound.execution.results import compare_tests, summarize_comparisons, validate_report

_SOURCE_BINDING = bind_source(__file__)
STATUSES = {"not_run", "unsupported", "interrupted", "running", "evaluated", "invalidated"}


def execution(value, *, image_id, suite=None, observation=False):
    if not isinstance(value, dict):
        raise ValueError("Missing execution evidence.")
    allowed = {field.name for field in fields(ExecutionResult)}
    if set(value) - allowed - {"target_imports"}:
        raise ValueError("Unexpected execution fields.")
    try:
        run = ExecutionResult(**{key: item for key, item in value.items() if key in allowed})
    except TypeError:
        raise ValueError("Malformed execution evidence.") from None
    if run.image_id != image_id:
        raise ValueError("Execution image identity differs.")
    labels = (
        DOCKER_CONTROLLERS
        if observation
        else (INDEPENDENT_CONTROLLERS if suite is not None else REPOSITORY_CONTROLLERS)
    )
    expected_controller = controller_binding(labels).to_dict()
    if run.controller_binding != expected_controller:
        raise ValueError("Execution controller identity differs.")
    if not observation:
        if suite is not None:
            adapter = Path(__file__).parents[1] / "execution/adapters/call_adapter.py"
            expected = hashlib.sha256(
                expected_controller["sha256"].encode()
                + regular_bytes(adapter, 512 * 1024)
                + suite.canonical_bytes()
            ).hexdigest()
            source = "external_json_assertions"
        else:
            harness = Path(__file__).parents[1] / "execution"
            expected = hashlib.sha256(
                expected_controller["sha256"].encode()
                + b"".join(
                    regular_bytes(harness / name, 512 * 1024)
                    for name in ("repository_pytest_runner.py", "pytest_runner.py", "pytest.ini")
                )
            ).hexdigest()
            source = REPOSITORY_SOURCE
        if run.evaluator_sha256 != expected or run.evidence_source != source:
            raise ValueError("Execution evaluator identity differs.")
    if run.test_report is not None:
        validate_report(run.test_report, run.exit_code)
        if suite is not None and set(run.test_report["collected"]) != {
            suite.name + "::" + case.id for case in suite.cases
        }:
            raise ValueError("Observed case inventory differs from the frozen profile.")
        if suite is not None:
            observations = run.case_evidence
            if not isinstance(observations, list) or len(observations) > len(suite.cases):
                raise ValueError("Missing bounded independent observations.")
            tests = {test["nodeid"]: test for test in run.test_report["tests"]}
            for case, retained in zip(suite.cases, observations):
                if set(retained) != {"case_id", "observation"} or retained["case_id"] != case.id:
                    raise ValueError("Observed case identity differs.")
                observed = execution(retained["observation"], image_id=image_id, observation=True)
                outcome, message = judge(case, observed)
                test = tests[suite.name + "::" + case.id]
                if (test["outcome"], test["message"], test["duration_seconds"]) != (
                    outcome,
                    message,
                    observed.duration_seconds,
                ):
                    raise ValueError("Reported outcome differs from independent observation.")
            if any(
                tests[suite.name + "::" + case.id]["outcome"] != "not_run"
                for case in suite.cases[len(observations) :]
            ):
                raise ValueError("Unobserved cases cannot have completed outcomes.")
    return run


def verified_decisions(row, config, image_id):
    """Recompute decisions from evidence rather than trusting artifact verdict strings."""
    decisions = {name: "abstain" for name in EVALUATORS}
    if row["status"] in {"unsupported", "not_run", "invalidated"}:
        return decisions
    if config is None:
        raise ValueError("Execution evidence has no bound operator configuration.")
    expected_profiles = {"visible": config.visible.sha256, "hidden": config.independent.sha256}
    if row.get("profile_sha256") != expected_profiles:
        raise ValueError("Frozen profile identity differs.")
    comparisons = {}
    for name, suite in (("visible", config.visible), ("hidden", config.independent)):
        evidence = row.get("suites", {}).get(name)
        if evidence is None:
            continue
        if evidence.get("test_suite_sha256") != suite.sha256:
            raise ValueError("Suite evidence identity differs.")
        baseline = execution(evidence["baseline"], image_id=image_id, suite=suite)
        if "candidate" not in evidence:
            continue
        candidate = execution(evidence["candidate"], image_id=image_id, suite=suite)
        comparison = compare_tests(baseline, candidate)
        if evidence.get("test_comparison") != comparison:
            raise ValueError("Recorded comparison differs from observed evidence.")
        comparisons[name] = {"test_comparison": comparison}
        if name == "visible":
            decisions["visible_only"] = visible_decision(candidate)
    if set(comparisons) == {"visible", "hidden"}:
        assessment = summarize_comparisons(comparisons)
        if row.get("assessment") != assessment:
            raise ValueError("Recorded independent assessment differs.")
        decisions["independent"] = independent_decision(assessment)
    repository = row.get("repository_tests")
    if repository:
        if config.repository_tests is None:
            raise ValueError("Repository evidence has no operator configuration.")
        if row.get("repository_configuration_sha256") != config.repository_tests.sha256:
            raise ValueError("Repository configuration identity differs.")
        if repository.get("status") == "completed":
            provenance = repository.get("provenance") or {}
            if provenance.get("configuration_sha256") != config.repository_tests.sha256:
                raise ValueError("Frozen repository test provenance differs.")
            baseline = execution(repository["baseline"], image_id=image_id)
            candidate = execution(repository["candidate"], image_id=image_id)
            compared = compare_tests(baseline, candidate)
            if repository.get("test_comparison") != compared:
                raise ValueError("Repository comparison differs from observed evidence.")
            decisions["repository_tests_only"] = visible_decision(candidate)
    static = row.get("static_analysis")
    if static:
        harness = Path(__file__).parents[1] / "execution"
        expected_static = hashlib.sha256(
            regular_bytes(harness / "static_analysis.py", 512 * 1024)
            + regular_bytes(harness / "inspection/static_ruff.py", 512 * 1024)
        ).hexdigest()
        if static.get("evaluator_sha256") != expected_static:
            raise ValueError("Static evaluator identity differs.")
        if static.get("image_id") != image_id:
            raise ValueError("Static image identity differs.")
        if static.get("status") == "completed":
            if static.get("counts") != {
                name: len(static[name]) for name in ("new", "resolved", "existing")
            }:
                raise ValueError("Static finding counts differ.")
            if not static.get("coverage", {}).get("comparison_complete"):
                raise ValueError("Static evidence contradicts its completion status.")
        decisions["static_only"] = static_decision(static)
    return decisions


def validate_evidence(record, corpus_sha, prepared, mapping, mapping_sha):
    if (
        record.get("schema_version") != 1
        or record.get("kind") != "label_blind_patch_corpus_execution"
        or record.get("corpus_sha256") != corpus_sha
        or record.get("mapping_sha256") != mapping_sha
        or record.get("status")
        not in {"running", "completed", "timeout", "cancelled", "invalidated"}
    ):
        raise ValueError("Execution artifact does not bind this corpus and mapping.")
    if record.get("controller_binding") != controller_binding(
        CONTROLLERS
    ).to_dict() or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(record.get("image_id", ""))):
        raise ValueError("Execution controller or image identity differs.")
    rows = record.get("rows")
    if not isinstance(rows, list) or len(rows) != len(prepared):
        raise ValueError("Execution artifact must retain every corpus row.")
    by_id = {row.get("case_id"): row for row in rows}
    if len(by_id) != len(rows) or set(by_id) != {item.case.id for item in prepared}:
        raise ValueError("Execution case IDs differ or repeat.")
    configurations = {task.task_id: task for task in mapping.tasks}
    verified = []
    for item in prepared:
        row = by_id[item.case.id]
        if (
            row.get("case_identity_sha256") != item.identity_sha256
            or row.get("patch_sha256") != item.case.patch_sha256
            or row.get("issue_sha256") != item.case.issue_sha256
            or row.get("task_id") != item.case.task_id
            or row.get("generation") != item.case.generation.model_dump(mode="json")
            or row.get("split") != item.case.split
            or row.get("group") != item.case.family
            or row.get("repository", "").casefold() != item.case.repository.casefold()
            or row.get("base_commit") != item.case.base_sha
            or row.get("status") not in STATUSES
        ):
            raise ValueError("Execution row identity differs from the retained case.")
        for field in ("baseline_workspace_sha256", "candidate_workspace_sha256"):
            if (field in row or row["status"] == "evaluated") and not re.fullmatch(
                r"[0-9a-f]{64}", str(row.get(field, ""))
            ):
                raise ValueError("Copied workspace identity is missing or malformed.")
        config = configurations.get(item.case.task_id)
        if config and (config.repository.casefold(), config.base_commit, config.issue_sha256) != (
            item.case.repository.casefold(),
            item.case.base_sha,
            item.case.issue_sha256,
        ):
            raise ValueError("Operator mapping differs from the corpus task.")
        decisions = verified_decisions(row, config, record["image_id"])
        if row.get("decisions") != decisions:
            raise ValueError("Recorded decisions differ from validated observed evidence.")
        if record["status"] == "invalidated" and any(
            value != "abstain" for value in decisions.values()
        ):
            raise ValueError("Invalidated evidence cannot contain decisions.")
        verified.append(row | {"decisions": decisions})
    return verified


def score(rows, evaluator):
    eligible = [row for row in rows if row["accuracy_eligible"]]
    confusion = {
        label: {decision: 0 for decision in ("accept", "reject", "abstain")}
        for label in ("valid", "invalid")
    }
    for row in eligible:
        confusion[row["label"]][row["decisions"][evaluator]] += 1
    valid = sum(confusion["valid"].values())
    invalid = sum(confusion["invalid"].values())
    abstentions = confusion["valid"]["abstain"] + confusion["invalid"]["abstain"]
    return {
        "total_cases": len(rows),
        "reviewed_cases": len(eligible),
        "excluded_unknown_or_ambiguous": len(rows) - len(eligible),
        "valid_denominator": valid,
        "invalid_denominator": invalid,
        "confusion": confusion,
        "bad_patches_caught": confusion["invalid"]["reject"],
        "bad_patches_missed": confusion["invalid"]["accept"],
        "invalid_abstentions": confusion["invalid"]["abstain"],
        "false_positives": confusion["valid"]["reject"],
        "valid_abstentions": confusion["valid"]["abstain"],
        "bad_patch_detection_rate": confusion["invalid"]["reject"] / invalid if invalid else None,
        "false_positive_rate": confusion["valid"]["reject"] / valid if valid else None,
        "reviewed_decision_coverage": (len(eligible) - abstentions) / len(eligible)
        if eligible
        else None,
        "all_case_decision_coverage": sum(row["decisions"][evaluator] != "abstain" for row in rows)
        / len(rows)
        if rows
        else None,
    }


def score_group(rows):
    assessed = [row for row in rows if row["status"] in {"evaluated", "interrupted"}]
    operator_passing = [row for row in rows if row["decisions"]["visible_only"] == "accept"]
    repository_passing = [
        row for row in rows if row["decisions"]["repository_tests_only"] == "accept"
    ]
    return {
        "all_cases": {name: score(rows, name) for name in EVALUATORS},
        "operator_probe_passing_cases": {
            name: score(operator_passing, name) for name in EVALUATORS
        },
        "repository_test_passing_cases": {
            name: score(repository_passing, name) for name in EVALUATORS
        },
        "incomplete_or_regressive": {
            "regressions": sum(
                (row.get("assessment") or {}).get("verdict") == "regression_detected"
                for row in assessed
            ),
            "incomplete": sum(
                (row.get("assessment") or {}).get("verdict") == "incomplete" for row in assessed
            ),
            "inconclusive": sum(
                (row.get("assessment") or {}).get("verdict") == "inconclusive" for row in assessed
            ),
        },
    }


def summarize(rows):
    result = score_group(rows)
    result["by_split"] = {
        split: score_group([row for row in rows if row["split"] == split])
        for split in sorted({row["split"] for row in rows})
    }
    result["by_group"] = {
        group: score_group([row for row in rows if row["group"] == group])
        for group in sorted({row["group"] for row in rows})
    }
    return result


def score_evidence(evidence_path, corpus_path, mapping_path, reviews_path):
    binding = controller_binding(
        (
            "benchmark/corpus_metrics.py",
            *CONTROLLERS,
            "benchmark/review.py",
        )
    )
    raw = regular_bytes(evidence_path, 32 * 1024 * 1024)
    record = load_evidence(raw, limit=32 * 1024 * 1024)
    corpus, corpus_sha, prepared = prepare_corpus(corpus_path)
    mapping, mapping_sha = prepare_mapping(mapping_path)
    rows = validate_evidence(record, corpus_sha, prepared, mapping, mapping_sha)
    reviews = load_reviews(reviews_path)
    joined = {row["case_id"]: row for row in join_reviews(prepared, reviews)}
    scored = [row | joined[row["case_id"]] for row in rows]
    result = {
        "schema_version": 1,
        "kind": "post_hoc_human_review_scoring",
        "execution_sha256": hashlib.sha256(raw).hexdigest(),
        "corpus_sha256": corpus_sha,
        "mapping_sha256": mapping_sha,
        "review_sha256": hashlib.sha256(regular_bytes(reviews_path, 4 * 1024 * 1024)).hexdigest(),
        "controller_binding": binding.to_dict(),
        "rows": scored,
        "metrics": summarize(scored),
        "limitations": [
            "Accuracy denominators use only agreed human review records; "
            "unknown, uncertain and conflicting reviews are excluded.",
            "Unsupported cases and unfinished runs remain abstentions "
            "within reviewed denominators.",
            "Operator probes are not proven tests visible to the original producing agent.",
            "Repository pytest and static-only screening are separate lower-trust baselines.",
            "A development pilot from one agent/source does not establish population accuracy.",
            "Human identity and review attestation are supplied records, "
            "not authenticated by software.",
            "Retained execution JSON is operator evidence, not cryptographically signed; "
            "source bindings do not authenticate a hostile complete artifact rewrite.",
        ],
    }
    binding.ensure_current()
    _, current_sha, current_prepared = prepare_corpus(corpus_path)
    if current_sha != corpus_sha or [item.identity_sha256 for item in current_prepared] != [
        item.identity_sha256 for item in prepared
    ]:
        raise ValueError("Corpus changed during scoring.")
    if prepare_mapping(mapping_path)[1] != mapping_sha or load_reviews(reviews_path) != reviews:
        raise ValueError("Scoring inputs changed.")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("evidence", "corpus", "mapping", "reviews", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    result = score_evidence(args.evidence, args.corpus, args.mapping, args.reviews)
    from codehound.benchmark.run import evidence_writer

    evidence_writer(args.output)(result)
    print(f"Post-hoc metrics saved to {args.output}")


if __name__ == "__main__":
    main()
