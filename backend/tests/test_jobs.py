import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session
from test_results import execution

from codehound.api import github, verifications
from codehound.db.jobs import JobStore
from codehound.db.models import ExecutionJob
from codehound.db.store import StoreConflict
from codehound.evaluation.registry import load_profiles
from codehound.execution import worker
from codehound.execution.results import compare_tests, summarize_comparisons
from codehound.main import app

HEADERS = {"X-CodeHound-Request": "1"}


@pytest.fixture
def ready(authenticated_client, github_bundle, monkeypatch, tmp_path):
    profile = load_profiles()["codehound-url-contract"].model_copy(
        update={"id": "test-profile", "repository": "octocat/project"}
    )
    directory = tmp_path / "profiles"
    directory.mkdir()
    (directory / "profile.json").write_text(profile.model_dump_json())
    monkeypatch.setenv("CODEHOUND_PROFILE_DIR", str(directory))
    monkeypatch.setenv("CODEHOUND_EXECUTION_IMAGE_ID", "sha256:" + "a" * 64)
    monkeypatch.setattr(verifications, "GitHubClient", lambda token: github_bundle)
    response = authenticated_client.post(
        "/api/verifications",
        json={
            "pr_url": "https://github.com/octocat/project/pull/7",
            "issue_text": "Verify the behavior independently.",
        },
        headers=HEADERS,
    )
    identifier = response.json()["id"]
    assert (
        authenticated_client.post(
            f"/api/verifications/{identifier}/intake", headers=HEADERS
        ).json()["status"]
        == "ready"
    )
    return authenticated_client, identifier, JobStore(app.state.database), profile


def enqueue(ready, key=None):
    client, identifier, _, _ = ready
    headers = HEADERS | ({"Idempotency-Key": key} if key else {})
    return client.post(
        f"/api/verifications/{identifier}/executions",
        json={"profile_id": "test-profile"},
        headers=headers,
    )


def artifact():
    before, after = execution({"example": "failed"}), execution({"example": "passed"})
    suites = {
        "visible": {
            "baseline": before.to_dict(),
            "candidate": after.to_dict(),
            "test_comparison": compare_tests(before, after),
        }
    }
    return {"schema_version": 2, "suites": suites, "assessment": summarize_comparisons(suites)}


def test_queue_idempotency_and_private_profile_boundary(ready):
    client, identifier, _, _ = ready
    profiles = client.get(f"/api/verifications/{identifier}/profiles").json()
    assert profiles["configured"] and not profiles["worker_online"]
    assert profiles["profiles"][0]["id"] == "test-profile"
    key = str(uuid4())
    first, repeated = enqueue(ready, key), enqueue(ready, key)
    assert first.status_code == 202 and repeated.status_code == 200
    assert first.json()["id"] == repeated.json()["id"]
    assert enqueue(ready).status_code == 409
    job = client.get(first.headers["Location"])
    assert job.json()["status"] == "queued"
    assert "profile_snapshot" not in job.text and '"expect":' not in job.text
    assert job.headers["cache-control"] == "no-store"
    assert (
        client.post(
            f"/api/verifications/{identifier}/executions",
            json={"profile_id": "test-profile", "image_id": "evil", "tests": "/etc"},
            headers=HEADERS,
        ).status_code
        == 422
    )


def test_every_execution_endpoint_is_owner_scoped(ready):
    client, identifier, _, _ = ready
    job_id = enqueue(ready).json()["id"]
    github.sessions["test-session"]["user"]["id"] = 999
    for path in (
        f"/api/executions/{job_id}",
        f"/api/executions/{job_id}/export",
        f"/api/verifications/{identifier}/executions",
        f"/api/verifications/{identifier}/profiles",
    ):
        assert client.get(path).status_code == 404
    assert client.post(f"/api/executions/{job_id}/cancel", headers=HEADERS).status_code == 404
    assert enqueue(ready).status_code == 404


def test_queue_rejects_unconfigured_image_or_unready_record(ready, monkeypatch):
    client, identifier, _, _ = ready
    monkeypatch.delenv("CODEHOUND_EXECUTION_IMAGE_ID")
    assert enqueue(ready).status_code == 503
    monkeypatch.setenv("CODEHOUND_EXECUTION_IMAGE_ID", "sha256:" + "a" * 64)
    new = client.post(
        "/api/verifications",
        json={
            "pr_url": "https://github.com/octocat/project/pull/8",
            "issue_text": "Verify behavior before execution.",
        },
        headers=HEADERS,
    ).json()["id"]
    assert (
        client.post(
            f"/api/verifications/{new}/executions",
            json={"profile_id": "test-profile"},
            headers=HEADERS,
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/verifications/{identifier}/executions", json={"profile_id": "test-profile"}
        ).status_code
        == 403
    )


def test_concurrent_claim_and_expired_worker_cannot_write(ready):
    _, identifier, store, profile = ready
    job_id = enqueue(ready).json()["id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(lambda _: store.claim(), range(2)))
    assert sum(job is not None for job in claimed) == 1
    job = next(job for job in claimed if job)
    with Session(store.engine) as db:
        db.execute(
            update(ExecutionJob)
            .where(ExecutionJob.id == job.id)
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )
        db.commit()
    store.reap()
    assert store.get(job_id, 123).status == "failed"
    with pytest.raises(StoreConflict):
        store.finish(job.id, job.claim_token, artifact=artifact())
    new, created = store.enqueue(identifier, 123, profile, job.image_id)
    assert created and new.id != job.id


def test_cancel_queued_and_running_jobs(ready):
    client, _, store, _ = ready
    first = enqueue(ready).json()["id"]
    assert (
        client.post(f"/api/executions/{first}/cancel", headers=HEADERS).json()["status"]
        == "cancelled"
    )
    assert store.claim() is None
    second = enqueue(ready).json()["id"]
    job = store.claim()
    assert job.id == second
    store.cancel(job.id, 123)
    store.finish(job.id, job.claim_token, artifact=artifact())
    final = store.get(job.id, 123)
    assert final.status == "cancelled" and final.artifact is None and final.assessment is None


def test_completed_results_are_persisted_exportable_and_limited_in_scope(ready):
    client, identifier, store, _ = ready
    enqueue(ready)
    job = store.claim()
    store.finish(job.id, job.claim_token, artifact=artifact())
    exported = client.get(f"/api/executions/{job.id}/export")
    assert exported.json()["artifact"] == artifact()
    assert exported.json()["assessment"]["verdict"] == "candidate_improves"
    assert '"expect":' not in exported.text
    report = client.get(f"/api/verifications/{identifier}").json()
    assert report["execution_status"] == "completed"
    assert report["latest_execution"]["id"] == job.id
    checks = {check["name"]: check["status"] for check in report["checks"]}
    assert checks["visible_tests"] == "pass"
    assert checks["task_completion"] == "not_run"
    assert checks["regression_safety"] == "unknown"
    assert report["confidence"] is None
    assert (
        client.get(f"/api/verifications/{identifier}/executions").json()[0]["assessment"]["verdict"]
        == "candidate_improves"
    )


def test_worker_uses_frozen_profile_and_exact_snapshot(ready, monkeypatch):
    _, _, store, _ = ready
    enqueue(ready)
    job = store.claim()
    calls = []

    async def docker(*args):
        return 0, b"", b""

    async def evaluate(snapshot, suites, runner, **kwargs):
        calls.append(snapshot["head_sha"])
        assert kwargs["mode"] == "independent"
        assert suites["visible"].name == "url-visible"
        await kwargs["on_progress"]("visible_candidate")
        return artifact()

    async def execute(claimed, database):
        return await worker.execute_job(claimed, database, executor=evaluate)

    monkeypatch.setattr(worker, "control", docker)
    asyncio.run(worker.process_job(job, app.state.database, asyncio.Event(), execute=execute))
    final = store.get(job.id, 123)
    assert final.status == "completed"
    assert calls == [job.head_sha]
    assert final.artifact["profile"]["id"] == "test-profile"


def test_worker_cancellation_waits_for_execution_cleanup(ready):
    _, _, store, _ = ready
    enqueue(ready)
    job = store.claim()
    cleaned = []

    async def run():
        started = asyncio.Event()

        async def execute(*_):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cleaned.append(True)

        work = asyncio.create_task(
            worker.process_job(
                job, app.state.database, asyncio.Event(), execute=execute, heartbeat_seconds=0.01
            )
        )
        await started.wait()
        store.cancel(job.id, 123)
        await asyncio.wait_for(work, timeout=2)

    asyncio.run(run())
    assert cleaned and store.get(job.id, 123).status == "cancelled"


def test_worker_presence_is_reported_and_removed(ready):
    _, _, store, _ = ready
    worker_id = str(uuid4())
    store.worker_heartbeat(worker_id)
    assert store.worker_online()
    store.worker_stopped(worker_id)
    assert not store.worker_online()
