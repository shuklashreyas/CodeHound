from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_results import execution

from codehound.evaluation.job_schemas import execution_checks
from codehound.evaluation.registry import EvaluationProfile, load_profiles
from codehound.evaluation.requirements import requirement_evidence
from codehound.evaluation.schemas import unrun_checks


def profile():
    return load_profiles()["codehound-url-contract"]


def artifact(visible=None, hidden=None):
    config = profile()
    suites = {}
    for name, overrides in (("visible", visible or {}), ("hidden", hidden or {})):
        suite = getattr(config, name)
        outcomes = {f"{suite.name}::{case.id}": "passed" for case in suite.cases}
        before = execution(outcomes).to_dict()
        after = execution(outcomes | overrides).to_dict()
        before["evidence_source"] = after["evidence_source"] = "external_json_assertions"
        before["evaluator_sha256"] = after["evaluator_sha256"] = "b" * 64
        suites[name] = {
            "baseline": before,
            "candidate": after,
            "test_comparison": {"regressions": []},
        }
    return {"suites": suites}


def test_requirement_mapping_references_must_exist_and_be_unique():
    original = profile().model_dump()
    for reference in (
        {"suite": "hidden", "case_id": "missing"},
        {"suite": "visible", "case_id": "wrong-host"},
    ):
        invalid = original | {
            "requirements": [{"id": "x", "description": "Task requirement", "cases": [reference]}]
        }
        with pytest.raises(ValidationError):
            EvaluationProfile.model_validate(invalid)
    requirement = original["requirements"][0]
    with pytest.raises(ValidationError):
        EvaluationProfile.model_validate(original | {"requirements": [requirement, requirement]})
    with pytest.raises(ValidationError):
        EvaluationProfile.model_validate(
            original | {"requirements": [requirement | {"cases": requirement["cases"] * 2}]}
        )


def test_public_profile_never_exposes_inputs_or_expectations():
    public = profile().public()
    assert public["requirements"][0]["mapped_cases"] == 2
    assert set(public["requirements"][0]) == {"id", "description", "mapped_cases"}
    assert "expect" not in str(public)


def test_requirements_capture_contradictions_and_missing_evidence():
    evidence = artifact(hidden={"url-independent::credentials": "failed"})
    result = requirement_evidence(profile(), evidence)
    row = result["requirements"][1]
    assert row["baseline"]["status"] == "supported_by_checks"
    assert row["candidate"]["status"] == "contradicted"
    assert row["candidate"]["failed"] == 1
    assert not result["unmapped_cases"]
    evidence["suites"]["hidden"]["candidate"]["status"] = "timeout"
    assert (
        requirement_evidence(profile(), evidence)["requirements"][1]["candidate"]["status"]
        == "unverified"
    )


def test_empty_requirements_and_unmapped_requirements_remain_unverified():
    original = profile().model_dump()
    config = EvaluationProfile.model_validate(
        original | {"requirements": [{"id": "future", "description": "Unimplemented coverage"}]}
    )
    result = requirement_evidence(config, artifact())
    assert result["requirements"][0]["candidate"]["status"] == "unmapped"
    assert len(result["unmapped_cases"]) == 8
    legacy = EvaluationProfile.model_validate(original | {"requirements": []})
    assert not requirement_evidence(legacy, artifact())["requirements"]


def test_invalid_or_changed_evidence_cannot_support_requirements():
    for field, value in (
        ("evaluator_sha256", "different"),
        ("evidence_source", "in_process_pytest"),
        ("evidence_error", "bad"),
    ):
        evidence = artifact()
        evidence["suites"]["visible"]["candidate"][field] = value
        assert (
            requirement_evidence(profile(), evidence)["requirements"][0]["candidate"]["status"]
            == "unverified"
        )
    evidence = artifact()
    evidence["suites"]["visible"]["candidate"]["test_report"]["tests"].pop()
    assert (
        requirement_evidence(profile(), evidence)["requirements"][0]["candidate"]["status"]
        == "unverified"
    )


def test_passing_examples_do_not_mark_entire_issue_solved():
    evidence = artifact()
    evidence["requirement_evidence"] = requirement_evidence(profile(), evidence)
    checks = unrun_checks()
    execution_checks(checks, SimpleNamespace(status="completed", artifact=evidence))
    by_name = {item.name: item for item in checks}
    assert by_name["requirement_adherence"].status == "unknown"
    assert "3 supported" in by_name["requirement_adherence"].explanation
    assert by_name["task_completion"].status == "not_run"
    evidence = artifact(hidden={"url-independent::credentials": "failed"})
    evidence["requirement_evidence"] = requirement_evidence(profile(), evidence)
    execution_checks(checks, SimpleNamespace(status="completed", artifact=evidence))
    assert by_name["requirement_adherence"].status == "fail"


def test_partial_valid_evidence_retains_requirement_contradiction():
    evidence = artifact(
        hidden={"url-independent::credentials": "failed", "url-independent::wrong-host": "not_run"}
    )
    run = evidence["suites"]["hidden"]["candidate"]
    run["status"] = "timeout"
    run["exit_code"] = run["test_report"]["exit_code"] = 2
    result = requirement_evidence(profile(), evidence)
    requirement = result["requirements"][1]
    assert requirement["candidate"]["status"] == "contradicted"
    assert requirement["candidate"]["failed"] == 1
    assert requirement["candidate"]["unverified"] == 1
