"""Distinct pinned upstream behavior fixes; source runs only in restricted Docker."""

import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest

from codehound.evaluation.registry import load_profiles
from codehound.execution import independent
from codehound.execution.real_repository import reproduce
from codehound.repositories.intake import ChangedFile, Pull

ROOT = Path(__file__).parents[1] / "fixtures" / "real_repository"
TASKS = [
    (403, "boltons-bytes-boundaries", "bytes2human", 4, 9, 2, 5, 16),
    (418, "boltons-singular-double-s", "singularize", 3, 12, 1, 7, 17),
]


def snapshot(number):
    return json.loads((ROOT / f"boltons-pr-{number}" / "snapshot.json").read_text())


def profile_binding(profile):
    """Match the canonical profile binding derived from retained execution evidence."""
    value = {
        "metadata": profile.public(),
        "requirements": [item.model_dump(mode="json") for item in profile.requirements],
        "suites": {"visible": profile.visible.sha256, "hidden": profile.hidden.sha256},
        "repository_tests": profile.repository_tests.sha256,
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@pytest.mark.parametrize(
    "number,identifier,function,visible,hidden,v_improved,h_improved,repo_tests", TASKS
)
def test_authentic_historical_metadata_diff_and_profile_contract(
    number,
    identifier,
    function,
    visible,
    hidden,
    v_improved,
    h_improved,
    repo_tests,
):
    directory = ROOT / f"boltons-pr-{number}"
    captured = snapshot(number)
    raw = (directory / "pull-request.json").read_bytes()
    pull = Pull.model_validate_json(raw)
    metadata = json.loads(raw)
    assert pull.number == number and pull.state == "closed" and pull.merged is True
    assert captured["pull_request"]["state"] == "closed"
    assert captured["pull_request"]["merged"] is True
    assert captured["pull_request"]["title"] == pull.title
    assert captured["head_sha"] == pull.head.sha
    assert captured["base_target_sha"] == pull.base.sha
    assert captured["repository"]["full_name"] == "mahmoud/boltons"
    assert captured["repository"]["private"] is False
    diff = (directory / "pinned.diff").read_text()
    assert captured["diff"] == diff
    assert captured["diff_sha256"] == hashlib.sha256(diff.encode()).hexdigest()
    assert captured["provenance"]["pull_metadata_sha256"] == hashlib.sha256(raw).hexdigest()
    assert captured["provenance"]["author"] == metadata["user"]["login"]
    assert captured["provenance"]["kind"] == "historical_upstream_patch"
    assert "not established" in captured["provenance"]["authorship_limit"]
    files = [ChangedFile.model_validate(file) for file in captured["files"]]
    assert {file.filename for file in files} == {"boltons/strutils.py", "tests/test_strutils.py"}
    assert sum(file.additions for file in files) == pull.additions
    assert sum(file.deletions for file in files) == pull.deletions
    profile = load_profiles()[identifier]
    assert profile.repository == "mahmoud/boltons"
    assert len(profile.visible.cases) == visible and len(profile.hidden.cases) == hidden
    for suite in (profile.visible, profile.hidden):
        assert suite.module == "boltons.strutils" and suite.function == function
        assert suite.source_directory == "." and suite.result_encoding == "json"
    inventory = {
        (name, case.id)
        for name, suite in (("visible", profile.visible), ("hidden", profile.hidden))
        for case in suite.cases
    }
    mapped = {
        (ref.suite, ref.case_id)
        for requirement in profile.requirements
        for ref in requirement.cases
    }
    assert mapped == inventory
    assert profile.repository_tests.test_paths == ["tests/test_strutils.py"]
    assert profile.repository_tests.target_packages == ["boltons"]
    observed = json.loads((directory / "observed-result.json").read_text())
    assert observed["base_sha"] == captured["base_sha"]
    assert observed["head_sha"] == captured["head_sha"]
    assert observed["diff_sha256"] == captured["diff_sha256"]
    assert observed["profile_id"] == identifier
    assert observed["profile_sha256"] == profile_binding(profile)
    harness = Path(independent.__file__).parent
    for name, suite in (("visible", profile.visible), ("hidden", profile.hidden)):
        retained = observed["independent_suites"][name]
        assert retained["test_suite_sha256"] == suite.sha256
        evaluator = hashlib.sha256(
            Path(independent.__file__).read_bytes()
            + (harness / "adapters" / "call_adapter.py").read_bytes()
            + (harness / "protocol.py").read_bytes()
            + suite.canonical_bytes()
        ).hexdigest()
        assert retained["evaluator_sha256"] == evaluator
    repository = observed["repository_tests"]
    provenance = repository["provenance"]
    assert provenance["baseline_sha"] == captured["base_sha"]
    assert provenance["configuration_sha256"] == profile.repository_tests.sha256
    assert provenance["test_paths"] == profile.repository_tests.test_paths
    assert provenance["source_directories"] == profile.repository_tests.source_directories
    assert provenance["target_packages"] == profile.repository_tests.target_packages
    assert provenance["file_count"] == len(provenance["files"]) == 3
    assert provenance["total_bytes"] == sum(item["bytes"] for item in provenance["files"])
    assert {item["path"] for item in provenance["files"]} == {
        "tests/test_strutils.py",
        "tests/conftest.py",
        "tests/__init__.py",
    }
    repository_evaluator = hashlib.sha256(
        b"".join(
            (harness / name).read_bytes()
            for name in ("repository_pytest_runner.py", "pytest_runner.py", "pytest.ini")
        )
    ).hexdigest()
    assert repository["evaluator_sha256"] == repository_evaluator
    assert observed["assessment"]["confidence"] is None
    assert observed["independent_suites"]["visible"]["counts"]["improvements"] == v_improved
    assert observed["independent_suites"]["hidden"]["counts"]["improvements"] == h_improved
    assert observed["repository_tests"]["counts"]["unchanged_passes"] == repo_tests


def test_additional_tasks_cover_distinct_behavior_and_preservation_examples():
    profiles = load_profiles()
    bytes_profile = profiles["boltons-bytes-boundaries"]
    singular_profile = profiles["boltons-singular-double-s"]
    assert bytes_profile.visible.function != singular_profile.visible.function
    bytes_cases = {case.id: case for case in bytes_profile.hidden.cases}
    assert bytes_cases["negative-kib"].args == [-1024]
    assert bytes_cases["boundary-digits"].kwargs == {"ndigits": 2}
    assert bytes_cases["below-mib-digits"].expect.value == "1024.0K"
    singular_cases = {case.id: case for case in singular_profile.hidden.cases}
    assert singular_cases["preserve-uppercase"].expect.value == "BOSS"
    assert singular_cases["plural-addresses"].expect.value == "address"


@pytest.mark.skipif(
    not os.getenv("CODEHOUND_TEST_IMAGE_ID")
    or os.getenv("CODEHOUND_RUN_PUBLIC_REPOSITORY_TESTS") != "1",
    reason="Explicit public Git checkout opt-in and a trusted Docker image required",
)
@pytest.mark.parametrize(
    "number,identifier,function,visible,hidden,v_improved,h_improved,repo_tests", TASKS
)
def test_live_pinned_tasks_expose_baseline_bug_and_pass_candidate_edge_cases(
    number,
    identifier,
    function,
    visible,
    hidden,
    v_improved,
    h_improved,
    repo_tests,
):
    artifact = asyncio.run(
        reproduce(
            snapshot(number), load_profiles()[identifier], os.environ["CODEHOUND_TEST_IMAGE_ID"]
        )
    )
    assert artifact["assessment"]["verdict"] == "candidate_improves"
    assert artifact["confidence"] is None
    for name, improvements, total in (
        ("visible", v_improved, visible),
        ("hidden", h_improved, hidden),
    ):
        suite = artifact["suites"][name]
        assert suite["test_comparison"]["counts"]["improvements"] == improvements
        assert suite["test_comparison"]["counts"]["regressions"] == 0
        assert suite["test_comparison"]["counts"]["unverified"] == 0
        tests = suite["candidate"]["test_report"]["tests"]
        assert len(tests) == total and all(test["outcome"] == "passed" for test in tests)
        assert suite["candidate"]["evidence_source"] == "external_json_assertions"
    assert artifact["requirement_evidence"]["unmapped_cases"] == []
    assert (
        artifact["repository_tests"]["test_comparison"]["counts"]["unchanged_passes"] == repo_tests
    )
    assert artifact["repository_tests"]["test_comparison"]["counts"]["regressions"] == 0
    assert (
        artifact["repository_tests"]["candidate"]["evidence_source"]
        == "repository_controlled_in_process_pytest"
    )
    assert artifact["static_analysis"]["status"] == "completed"
