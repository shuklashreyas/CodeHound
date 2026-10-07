"""Work deadlines retain observations without converting user cancellation into success."""

import asyncio
import os
from dataclasses import replace
from pathlib import Path

import pytest
import test_jobs

from codehound.execution import independent, verify, worker
from codehound.execution.deadlines import current_budget, execution_budget, optional_stage
from codehound.execution.docker import ExecutionResult
from codehound.execution.independent import IndependentRunner
from codehound.execution.profiles import TrustedSuite
from codehound.execution.repository_tests import RepositoryTestConfig
from codehound.main import app
from codehound.repositories.checkout import Checkouts
from codehound.repositories.intake import collect_snapshot
from codehound.repositories.urls import parse_pull_url

IMAGE = "sha256:" + "a" * 64
OBSERVATION = ExecutionResult("completed", 0, "", "", 0, IMAGE, False, False, 30)
ready = test_jobs.ready  # Reuse the real queued-verification fixture.


def observation(case, *, passed=True):
    if passed and case.expect.kind == "exception":
        exception = case.expect.exception
        return replace(
            OBSERVATION,
            call_response={
                "kind": "raised",
                "exception": exception if "." in exception else "builtins." + exception,
            },
        )
    value = case.expect.value if passed else {"deliberately_different": True}
    return replace(OBSERVATION, call_response={"kind": "returned", "value": value})


def suite(name):
    return TrustedSuite.model_validate(
        {
            "name": name,
            "module": "example",
            "function": "answer",
            "cases": [
                {"id": name + str(i), "expect": {"kind": "value", "value": i}} for i in range(3)
            ],
        }
    )


class Workspace:
    cleanup = None

    def __init__(self, repository, baseline, candidate):
        self.base_sha, self.head_sha = baseline, candidate

    async def __aenter__(self):
        return Checkouts(Path("baseline"), Path("candidate"), self.base_sha, self.head_sha)

    async def __aexit__(self, *_):
        if self.cleanup:
            await self.cleanup()


async def captured(bundle):
    return await collect_snapshot(
        parse_pull_url("https://github.com/octocat/project/pull/7"), bundle
    )


@pytest.mark.parametrize("stalled_stage", ["static_analysis", "repository_tests"])
def test_optional_stall_retains_completed_evidence_in_real_job_store(
    ready, monkeypatch, stalled_stage
):
    _, identifier, store, profile = ready
    selected = profile.model_copy(
        update={"repository_tests": RepositoryTestConfig(test_paths=["tests/test_example.py"])}
    )
    store.enqueue(identifier, 123, selected, IMAGE)
    job = store.claim()
    stages, cleaned = [], []

    async def docker(*_):
        return 0, b"", b""

    async def observe(self, workspace, profile, case):
        stages.append("independent:" + str(workspace))
        return observation(case, passed=str(workspace) == "baseline")

    async def inspect(*_):
        return {"status": "completed"}

    async def stalled(*args, **kwargs):
        stages.append(stalled_stage)
        try:
            await asyncio.Future()
        finally:
            # An evaluator's cancellation cleanup is allowed to complete.
            await asyncio.sleep(0.02)
            cleaned.append("optional")

    async def cleanup():
        cleaned.append("workspace")

    async def execute(claimed, database):
        async def evaluate(snapshot, suites, runner, **kwargs):
            return await verify.verify_snapshot(
                snapshot, suites, runner, workspace_factory=Workspace, **kwargs
            )

        return await worker.execute_job(claimed, database, executor=evaluate)

    monkeypatch.setattr(worker, "control", docker)
    monkeypatch.setattr(worker, "execution_budget", lambda: execution_budget(0.2))
    monkeypatch.setattr(worker, "HARD_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(independent, "SUITE_CLEANUP_SECONDS", 0)
    monkeypatch.setattr(IndependentRunner, "observe", observe)
    monkeypatch.setattr(Workspace, "cleanup", staticmethod(cleanup))
    for name in ("analyze_test_integrity", "analyze_python_impact", "analyze_static"):
        monkeypatch.setattr(worker, name, inspect)
    if stalled_stage == "static_analysis":
        monkeypatch.setattr(worker, "analyze_static", stalled)
    else:
        monkeypatch.setattr(verify, "run_repository_tests", stalled)
    asyncio.run(worker.process_job(job, app.state.database, asyncio.Event(), execute=execute))
    final = store.get(job.id, 123)
    assert final.status == "completed" and final.failure is None
    assert final.artifact["assessment"]["verdict"] == "regression_detected"
    assert final.assessment["verdict"] == "regression_detected"
    assert cleaned == ["optional", "workspace"]
    assert stages.index(stalled_stage) > max(
        i for i, stage in enumerate(stages) if stage.startswith("independent:")
    )
    budget = final.artifact["execution_budget"]
    assert budget["status"] == "exhausted" and stalled_stage in budget["incomplete_stages"]
    optional = final.artifact[stalled_stage]
    assert optional["status"] == "inconclusive"
    assert final.artifact["repository_tests"]["comparison"] == "inconclusive"
    assert final.artifact["repository_tests"]["affects_assessment"] is False
    if stalled_stage == "static_analysis":
        assert optional["coverage"]["comparison_complete"] is False
        assert optional["baseline"]["errors"] and optional["candidate"]["errors"]
        assert "repository_tests" not in stages  # Later stages do not start another container.


def test_partial_budget_keeps_every_expected_suite_revision_and_case(github_bundle, monkeypatch):
    calls = []

    async def observe(self, workspace, profile, case):
        calls.append((str(workspace), profile.name, case.id))
        if profile.name == "hidden" and case.id == "hidden1":
            await asyncio.Future()
        return observation(case, passed=str(workspace) == "candidate")

    async def run():
        snapshot = await captured(github_bundle)
        with execution_budget(0.1):
            return await verify.verify_snapshot(
                snapshot,
                {"visible": suite("visible"), "hidden": suite("hidden")},
                IndependentRunner(IMAGE),
                mode="independent",
                workspace_factory=Workspace,
            )

    monkeypatch.setattr(independent, "SUITE_CLEANUP_SECONDS", 0)
    monkeypatch.setattr(IndependentRunner, "observe", observe)
    artifact = asyncio.run(run())
    assert artifact["suites"]["visible"]["comparison"] == "candidate_improves"
    assert artifact["assessment"]["verdict"] == "inconclusive"
    assert set(artifact["suites"]) == {"visible", "hidden"}
    for result in artifact["suites"].values():
        for revision in ("baseline", "candidate"):
            report = result[revision]["test_report"]
            assert len(report["collected"]) == len(report["tests"]) == 3
    hidden = artifact["suites"]["hidden"]
    assert [case["outcome"] for case in hidden["baseline"]["test_report"]["tests"]] == [
        "failed",
        "not_run",
        "not_run",
    ]
    assert all(case["outcome"] == "not_run" for case in hidden["candidate"]["test_report"]["tests"])
    assert not any(path == "candidate" and name == "hidden" for path, name, _ in calls)
    assert hidden["test_comparison"]["counts"]["unverified"] == 3
    assert artifact["execution_budget"]["incomplete_stages"] == ["independent_suite:hidden"]


def test_cleanup_reserve_prevents_starting_cases_with_insufficient_work_budget(monkeypatch):
    async def unexpected(*_):
        pytest.fail("No container should start during the cleanup reserve")

    async def run():
        with execution_budget(1):
            return await IndependentRunner(IMAGE).run(Path("baseline"), suite("visible"))

    monkeypatch.setattr(IndependentRunner, "observe", unexpected)
    result = asyncio.run(run())
    assert result.status == "timeout" and result.timeout_seconds == 0
    assert all(case["outcome"] == "not_run" for case in result.test_report["tests"])


@pytest.mark.skipif(
    not os.getenv("CODEHOUND_TEST_IMAGE_ID"), reason="Trusted Docker image required"
)
def test_real_container_deadline_retains_first_case_and_stops_remaining_cases(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o755)
    (workspace / "example.py").write_text(
        "import time\ndef answer(value):\n    if value == 0: time.sleep(60)\n    return value\n"
    )
    trusted = TrustedSuite.model_validate(
        {
            "name": "bounded-real",
            "module": "example",
            "function": "answer",
            "source_directory": ".",
            "cases": [
                {"id": str(value), "args": [value], "expect": {"kind": "value", "value": value}}
                for value in (1, 0, 2)
            ],
        }
    )

    async def run():
        with execution_budget(3):
            return await IndependentRunner(os.environ["CODEHOUND_TEST_IMAGE_ID"]).run(
                workspace, trusted
            )

    monkeypatch.setattr(independent, "SUITE_CLEANUP_SECONDS", 0.5)
    result = asyncio.run(run())
    assert result.status == "timeout"
    assert [case["outcome"] for case in result.test_report["tests"]] == [
        "passed",
        "not_run",
        "not_run",
    ]
    assert len(result.case_evidence) == 1
    assert result.duration_seconds < 10  # Does not wait for the candidate's 60-second sleep.


@pytest.mark.parametrize("where", ["optional", "independent"])
def test_internal_timeout_error_is_not_mislabeled_as_budget_exhaustion(monkeypatch, where):
    async def internally_failed(*_):
        raise TimeoutError("Internal infrastructure timeout")

    async def run():
        with execution_budget(60):
            if where == "optional":
                await optional_stage("static_analysis", internally_failed, lambda: pytest.fail())
            else:
                await IndependentRunner(IMAGE).run(Path("baseline"), suite("visible"))

    monkeypatch.setattr(IndependentRunner, "observe", internally_failed)
    with pytest.raises(TimeoutError, match="Internal infrastructure"):
        asyncio.run(run())


def test_external_cancellation_propagates_and_resets_task_budget():
    async def run():
        started, cleaned = asyncio.Event(), []

        async def stalled():
            started.set()
            try:
                await asyncio.Future()
            finally:
                cleaned.append(True)

        async def inspect():
            with execution_budget(60):
                return await optional_stage("static_analysis", stalled, lambda: pytest.fail())

        work = asyncio.create_task(inspect())
        await started.wait()
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert cleaned and current_budget() is None

    asyncio.run(run())


def test_user_cancellation_during_optional_inspection_still_discards_artifact(ready, monkeypatch):
    _, identifier, store, profile = ready
    store.enqueue(identifier, 123, profile, IMAGE)
    job = store.claim()
    cleaned = []

    async def docker(*_):
        return 0, b"", b""

    async def observe(self, workspace, profile, case):
        return observation(case, passed=True)

    async def inspect(*_):
        return {"status": "completed"}

    async def cleanup():
        cleaned.append("workspace")

    async def run():
        started = asyncio.Event()

        async def stalled(*_):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cleaned.append("optional")

        async def execute(claimed, database):
            async def evaluate(snapshot, suites, runner, **kwargs):
                return await verify.verify_snapshot(
                    snapshot, suites, runner, workspace_factory=Workspace, **kwargs
                )

            return await worker.execute_job(claimed, database, executor=evaluate)

        monkeypatch.setattr(worker, "analyze_static", stalled)
        work = asyncio.create_task(
            worker.process_job(
                job, app.state.database, asyncio.Event(), execute=execute, heartbeat_seconds=0.01
            )
        )
        await started.wait()
        store.cancel(job.id, 123)
        await asyncio.wait_for(work, timeout=2)

    monkeypatch.setattr(worker, "control", docker)
    monkeypatch.setattr(IndependentRunner, "observe", observe)
    monkeypatch.setattr(Workspace, "cleanup", staticmethod(cleanup))
    monkeypatch.setattr(worker, "analyze_test_integrity", inspect)
    monkeypatch.setattr(worker, "analyze_python_impact", inspect)
    asyncio.run(run())
    final = store.get(job.id, 123)
    assert final.status == "cancelled" and final.artifact is None and final.assessment is None
    assert cleaned == ["optional", "workspace"]


def test_all_optional_deadline_fallbacks_have_complete_consumer_shapes():
    for stage in ("test_integrity", "repository_impact", "static_analysis", "repository_tests"):
        evidence = verify.incomplete_inspection(stage, IMAGE)
        assert evidence["status"] == "inconclusive" and evidence["limitations"]
    static = verify.incomplete_inspection("static_analysis", IMAGE)
    assert static["tool"]["version"] and len(static["evaluator_sha256"]) == 64
    assert static["counts"] == {"new": 0, "resolved": 0, "existing": 0}
    assert set(static["coverage"]) == {
        "baseline_files",
        "candidate_files",
        "excluded_directories",
        "comparison_complete",
    }
