"""Opt-in public GitHub intake through durable API/worker evidence exports."""

import asyncio
import os
from uuid import uuid4

import pytest

from codehound.api import verifications
from codehound.db.jobs import JobStore
from codehound.execution.worker import process_job
from codehound.main import app
from codehound.repositories.github_client import GitHubClient


@pytest.mark.skipif(
    not os.getenv("CODEHOUND_TEST_IMAGE_ID")
    or os.getenv("CODEHOUND_RUN_PUBLIC_REPOSITORY_TESTS") != "1",
    reason="Requires explicit public GitHub access and a trusted Docker image",
)
def test_public_historical_pr_reaches_persisted_independent_evidence(
    authenticated_client, monkeypatch
):
    # Authentication alone uses the isolated test session. All upstream reads,
    # checkout, execution, queue transitions, and export serialization are real.
    # Never send that synthetic session token to GitHub.
    monkeypatch.setattr(verifications, "GitHubClient", lambda _token: GitHubClient(None))
    monkeypatch.setenv("CODEHOUND_EXECUTION_IMAGE_ID", os.environ["CODEHOUND_TEST_IMAGE_ID"])
    headers = {"X-CodeHound-Request": "1", "Idempotency-Key": str(uuid4())}
    client = authenticated_client
    created = client.post(
        "/api/verifications",
        json={
            "pr_url": "https://github.com/pypa/packaging/pull/925",
            "issue_text": (
                "Reject trailing LF in validated names while preserving optional validation."
            ),
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    identifier = created.json()["id"]
    route = f"/api/verifications/{identifier}"
    intake = client.post(f"{route}/intake", headers=headers)
    assert intake.status_code == 200, intake.text
    report = intake.json()
    assert report["status"] == "ready", report.get("failure")
    assert report["execution_status"] == "not_run"
    assert report["snapshot"]["pull_request"]["merged"] is True
    assert all(check["status"] != "pass" for check in report["checks"])
    available = client.get(f"{route}/profiles").json()
    assert available["configured"] is True
    assert "packaging-name-validation" in {p["id"] for p in available["profiles"]}

    headers["Idempotency-Key"] = str(uuid4())
    queued = client.post(
        f"{route}/executions",
        json={"profile_id": "packaging-name-validation"},
        headers=headers,
    )
    assert queued.status_code == 202, queued.text
    execution_id = queued.json()["id"]
    queue = JobStore(app.state.database)
    claimed = queue.claim()
    assert claimed.id == execution_id

    async def execute():
        await process_job(claimed, app.state.database, asyncio.Event())

    asyncio.run(execute())
    result = client.get(f"/api/executions/{execution_id}")
    assert result.status_code == 200, result.text
    execution = result.json()
    assert execution["status"] == "completed", execution["failure"]
    assert execution["assessment"]["verdict"] == "candidate_improves"
    evidence = execution["artifact"]
    assert evidence["confidence"] is None
    assert evidence["suites"]["visible"]["test_comparison"]["counts"]["improvements"] == 1
    assert evidence["suites"]["hidden"]["test_comparison"]["counts"]["improvements"] == 2
    assert evidence["repository_tests"]["test_comparison"]["counts"]["unchanged_passes"] == 54
    assert evidence["static_analysis"]["status"] == "completed"
    assert evidence["requirement_evidence"]["unmapped_cases"] == []

    saved = client.get(route).json()
    assert saved["execution_status"] == "completed"
    assert saved["latest_execution"]["id"] == execution_id
    checks = {check["name"]: check for check in saved["checks"]}
    assert checks["visible_tests"]["status"] == checks["hidden_tests"]["status"] == "pass"
    assert checks["task_completion"]["status"] != "pass"
    assert client.get(f"{route}/export").json() == saved
    assert client.get(f"/api/executions/{execution_id}/export").json() == execution
    # Authentication remains owner scoped even after the worker writes evidence.
    client.cookies.clear()
    assert client.get(f"/api/executions/{execution_id}/export").status_code == 401
