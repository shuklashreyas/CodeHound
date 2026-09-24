import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from codehound.execution.docker import ExecutionResult
from codehound.execution.verify import comparison, suite_digest, verify_snapshot
from codehound.repositories.checkout import Checkouts
from codehound.repositories.intake import collect_snapshot
from codehound.repositories.urls import parse_pull_url

RESULT = ExecutionResult("completed", 0, "", "", 0.1, "sha256:" + "a" * 64, False, False, 30)


def run_result(exit_code):
    report = {
        "schema_version": 1,
        "exit_code": exit_code,
        "collected": ["test_case.py::test_example"],
        "tests": [
            {
                "nodeid": "test_case.py::test_example",
                "outcome": "passed" if exit_code == 0 else "failed",
                "duration_seconds": 0.1,
                "message": "",
            }
        ],
        "collection_errors": [],
    }
    return replace(RESULT, exit_code=exit_code, test_report=report)


@pytest.mark.parametrize(
    "first,second,expected",
    [
        (0, 0, "no_behavior_change_observed"),
        (0, 1, "regression_detected"),
        (1, 0, "candidate_improves"),
        (1, 1, "incomplete"),
        (0, 5, "inconclusive"),
        (2, 0, "inconclusive"),
    ],
)
def test_test_transitions(first, second, expected):
    assert comparison(run_result(first), run_result(second)) == expected


def test_timeout_cannot_count_as_improvement():
    assert comparison(replace(RESULT, status="timeout", exit_code=1), RESULT) == "inconclusive"


def test_suite_identity_and_symlinks(tmp_path):
    test = tmp_path / "test_case.py"
    test.write_text("assert True")
    original = suite_digest(tmp_path)
    test.write_text("assert False")
    assert suite_digest(tmp_path) != original
    test.unlink()
    test.symlink_to(Path(__file__).resolve())
    with pytest.raises(ValueError, match="symlinks"):
        suite_digest(tmp_path)


def test_pinned_comparison_uses_same_tests_and_cleans_up(github_bundle, tmp_path):
    snapshot = asyncio.run(
        collect_snapshot(parse_pull_url("https://github.com/octocat/project/pull/7"), github_bundle)
    )
    (tmp_path / "test_example.py").write_text("def test_example(): pass")
    calls = []

    class Workspace:
        def __init__(self, repository, baseline, candidate):
            assert repository == "octocat/project"
            assert baseline == "c" * 40 and candidate == "b" * 40

        async def __aenter__(self):
            return Checkouts(Path("baseline"), Path("candidate"), "c" * 40, "b" * 40)

        async def __aexit__(self, *_):
            calls.append("cleanup")

    class Runner:
        async def run(self, code, tests):
            calls.append((code, tests))
            return run_result(1 if str(code) == "baseline" else 0)

    result = asyncio.run(
        verify_snapshot(snapshot, {"visible": tmp_path}, Runner(), workspace_factory=Workspace)
    )
    assert result["suites"]["visible"]["comparison"] == "candidate_improves"
    assert result["confidence"] is None
    assert calls == [(Path("baseline"), tmp_path), (Path("candidate"), tmp_path), "cleanup"]
    snapshot["diff"] += "tampered"
    with pytest.raises(ValueError, match="checksum"):
        asyncio.run(
            verify_snapshot(snapshot, {"visible": tmp_path}, Runner(), workspace_factory=Workspace)
        )
    assert len(calls) == 3


def test_integrity_runs_inside_pinned_workspace_before_tests(github_bundle, tmp_path):
    snapshot = asyncio.run(
        collect_snapshot(parse_pull_url("https://github.com/octocat/project/pull/7"), github_bundle)
    )
    (tmp_path / "test_example.py").write_text("def test_example(): pass")
    stages = []
    closed = []

    class Workspace:
        def __init__(self, *_):
            pass

        async def __aenter__(self):
            return Checkouts(Path("base"), Path("head"), snapshot["base_sha"], snapshot["head_sha"])

        async def __aexit__(self, *_):
            closed.append(True)

    class Runner:
        image_id = RESULT.image_id

        async def run(self, *_):
            return run_result(0)

    async def inspect(captured, checkouts, image):
        assert captured == snapshot and checkouts.candidate == Path("head")
        assert image == RESULT.image_id and not closed
        return {"status": "inconclusive", "findings": [], "unverified": [{"reason": "timeout"}]}

    async def progress(stage):
        stages.append(stage)

    artifact = asyncio.run(
        verify_snapshot(
            snapshot,
            {"visible": tmp_path},
            Runner(),
            workspace_factory=Workspace,
            integrity_analyzer=inspect,
            on_progress=progress,
        )
    )
    assert closed and artifact["test_integrity"]["status"] == "inconclusive"
    assert artifact["suites"]["visible"]["comparison"] == "no_behavior_change_observed"
    assert stages == ["checkout", "test_integrity", "visible_baseline", "visible_candidate"]
