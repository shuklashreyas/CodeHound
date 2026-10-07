"""Offline provenance checks plus an explicitly enabled public-repository smoke."""

import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest

from codehound.evaluation.registry import load_profiles
from codehound.execution.real_repository import reproduce
from codehound.repositories.intake import ChangedFile, Pull

FIXTURE = Path(__file__).parents[1] / "fixtures" / "real_repository" / "packaging-pr-925"
BASE = "033854a05229074ddb191d67da1f8e0165e665da"
HEAD = "ca4e142149c04000c91c9f16642452e999ee04ba"


def snapshot():
    return json.loads((FIXTURE / "snapshot.json").read_text())


def test_historical_fixture_preserves_actual_public_pr_metadata_and_diff():
    captured = snapshot()
    metadata = (FIXTURE / "pull-request.json").read_bytes()
    pull = Pull.model_validate_json(metadata)
    assert pull.number == 925 and pull.state == "closed"
    assert captured["pull_request"]["state"] == "closed"
    assert captured["pull_request"]["title"] == pull.title
    assert captured["pull_request"]["body"] == pull.body
    assert captured["base_sha"] == captured["base_target_sha"] == pull.base.sha == BASE
    assert captured["head_sha"] == pull.head.sha == HEAD
    assert captured["repository"]["full_name"] == "pypa/packaging"
    assert captured["repository"]["private"] is False
    diff = (FIXTURE / "pinned.diff").read_text()
    assert captured["diff"] == diff
    assert hashlib.sha256(diff.encode()).hexdigest() == captured["diff_sha256"]
    provenance = captured["provenance"]
    assert hashlib.sha256(metadata).hexdigest() == provenance["pull_metadata_sha256"]
    assert provenance["kind"] == "historical_upstream_human_patch"
    assert provenance["author"] == json.loads(metadata)["user"]["login"]
    assert provenance["merged"] is True
    files = [ChangedFile.model_validate(item) for item in captured["files"]]
    assert {item.filename for item in files} == {"src/packaging/utils.py", "tests/test_utils.py"}
    assert len(files) == captured["summary"]["files_changed"] == pull.changed_files
    assert sum(item.additions for item in files) == pull.additions
    assert sum(item.deletions for item in files) == pull.deletions
    assert diff.count("diff --git ") == len(files)
    assert '+    r"^([A-Z0-9]|[A-Z0-9][A-Z0-9._-]*[A-Z0-9])\\Z"' in diff


def test_real_profile_maps_independent_cases_and_freezes_existing_tests():
    profile = load_profiles()["packaging-name-validation"]
    assert profile.repository == "pypa/packaging"
    assert profile.public()["visible_cases"] == 3
    assert profile.public()["hidden_cases"] == 13
    assert profile.repository_tests.test_paths == ["tests/test_utils.py"]
    assert profile.repository_tests.target_packages == ["packaging"]
    inventory = {
        (name, case.id)
        for name, suite in (("visible", profile.visible), ("hidden", profile.hidden))
        for case in suite.cases
    }
    mapped = {
        (reference.suite, reference.case_id)
        for requirement in profile.requirements
        for reference in requirement.cases
    }
    assert mapped == inventory
    for suite in (profile.visible, profile.hidden):
        assert suite.module == "packaging.utils" and suite.function == "canonicalize_name"
        assert suite.source_directory == "src" and suite.result_encoding == "json"
    trailing = next(case for case in profile.visible.cases if case.id == "reject-trailing-lf")
    assert trailing.args[0].endswith("\n") and trailing.kwargs == {"validate": True}
    assert trailing.expect.exception == "packaging.utils.InvalidName"
    preservation = next(
        case for case in profile.hidden.cases if case.id == "preserve-unvalidated-newline"
    )
    assert preservation.kwargs == {"validate": False}
    assert preservation.expect.value == "mixed-name\n"


def test_reproduction_passes_all_operator_checks_and_preserves_historical_provenance(monkeypatch):
    profile = load_profiles()["packaging-name-validation"]
    captured = snapshot()
    calls = []

    async def verify(value, suites, runner, **options):
        calls.append(options)
        assert value == captured
        assert suites == {"visible": profile.visible, "hidden": profile.hidden}
        assert runner.image_id == "sha256:" + "a" * 64
        return {"suites": {}, "limitations": []}

    monkeypatch.setattr("codehound.execution.real_repository.verify_snapshot", verify)
    artifact = asyncio.run(reproduce(captured, profile, "sha256:" + "a" * 64))
    options = calls[0]
    assert options["mode"] == "independent"
    assert options["repository_test_config"] == profile.repository_tests
    assert options["integrity_analyzer"].__name__ == "analyze_test_integrity"
    assert options["impact_analyzer"].__name__ == "analyze_python_impact"
    assert options["static_analyzer"].__name__ == "analyze_static"
    assert artifact["example_provenance"] == captured["provenance"]
    assert artifact["evaluation_profile"]["id"] == profile.id
    assert len(artifact["requirement_evidence"]["requirements"]) == 4
    assert artifact["requirement_evidence"]["unmapped_cases"] == []


def test_reproduction_rejects_mismatched_operator_profile():
    profile = load_profiles()["codehound-verdict-contract"]
    with pytest.raises(ValueError, match="repositories differ"):
        asyncio.run(reproduce(snapshot(), profile, "sha256:" + "a" * 64))


@pytest.mark.skipif(
    not os.getenv("CODEHOUND_TEST_IMAGE_ID")
    or os.getenv("CODEHOUND_RUN_PUBLIC_REPOSITORY_TESTS") != "1",
    reason="Explicit public Git checkout opt-in and a trusted Docker image required",
)
def test_pinned_upstream_bugfix_improves_independent_cases_without_repository_regressions():
    profile = load_profiles()["packaging-name-validation"]
    artifact = asyncio.run(reproduce(snapshot(), profile, os.environ["CODEHOUND_TEST_IMAGE_ID"]))
    for name, improvements in (("visible", 1), ("hidden", 2)):
        suite = artifact["suites"][name]
        assert suite["comparison"] == "candidate_improves", suite
        assert suite["test_comparison"]["counts"]["improvements"] == improvements
        assert suite["test_comparison"]["counts"]["regressions"] == 0
        assert suite["candidate"]["evidence_source"] == "external_json_assertions"
        assert all(
            case["outcome"] == "passed" for case in suite["candidate"]["test_report"]["tests"]
        )
    existing = artifact["repository_tests"]
    assert existing["status"] == "completed", existing
    assert existing["affects_assessment"] is False
    assert existing["comparison"] == "no_behavior_change_observed", existing
    assert existing["provenance"]["baseline_sha"] == BASE
    for revision in ("baseline", "candidate"):
        assert existing[revision]["exit_code"] == 0, existing[revision]
        assert existing[revision]["evidence_source"] == "repository_controlled_in_process_pytest"
    assert artifact["confidence"] is None
    assert artifact["base_sha"] == BASE and artifact["head_sha"] == HEAD
    assert artifact["test_integrity"] is not None
    assert artifact["python_impact"] is not None
    assert artifact["static_analysis"] is not None
    assert all(
        row["candidate"]["status"] == "supported_by_checks"
        for row in artifact["requirement_evidence"]["requirements"]
    )
