from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from codehound.api import github, verifications
from codehound.db.models import Verification
from codehound.db.store import StoreConflict, VerificationStore
from codehound.main import app
from codehound.repositories.github_client import GitHubFailure

SUBMISSION = {
    "pr_url": "https://github.com/octocat/project/pull/7",
    "issue_text": "Refresh tokens must remain valid.",
}
HEADERS = {"X-CodeHound-Request": "1"}


def create(client, **changes):
    response = client.post("/api/verifications", json=SUBMISSION | changes, headers=HEADERS)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_create_and_list_are_owner_scoped(authenticated_client):
    client = authenticated_client
    identifier = create(client)
    report = client.get(f"/api/verifications/{identifier}").json()
    assert report["status"] == "draft"
    assert report["confidence"] is None
    assert report["execution_status"] == "not_run"
    assert len(report["checks"]) == 13
    assert {c["status"] for c in report["checks"]} == {"not_run"}
    assert client.get("/api/verifications").json()["items"][0]["id"] == identifier
    github.sessions["test-session"]["user"]["id"] = 456
    assert client.get(f"/api/verifications/{identifier}").status_code == 404
    assert client.get(f"/api/verifications/{identifier}/export").status_code == 404
    assert (
        client.post(f"/api/verifications/{identifier}/intake", headers=HEADERS).status_code == 404
    )
    assert client.get("/api/verifications").json()["items"] == []


def test_write_origin_and_session_required(authenticated_client):
    client = authenticated_client
    assert client.post("/api/verifications", json=SUBMISSION).status_code == 403
    assert (
        client.post(
            "/api/verifications", json=SUBMISSION, headers=HEADERS | {"Origin": "https://evil.test"}
        ).status_code
        == 403
    )
    client.cookies.clear()
    assert client.post("/api/verifications", json=SUBMISSION, headers=HEADERS).status_code == 401


def test_idempotent_create_and_conflict(authenticated_client):
    headers = HEADERS | {"Idempotency-Key": str(uuid4())}
    first = authenticated_client.post("/api/verifications", json=SUBMISSION, headers=headers)
    second = authenticated_client.post("/api/verifications", json=SUBMISSION, headers=headers)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    conflict = authenticated_client.post(
        "/api/verifications",
        json=SUBMISSION | {"issue_text": "A different requirement entirely."},
        headers=headers,
    )
    assert conflict.status_code == 409


def test_saved_record_survives_app_restart(authenticated_client):
    identifier = create(authenticated_client)
    with TestClient(app) as restarted:
        restarted.cookies.set(github.SESSION_COOKIE, "test-session")
        assert (
            restarted.get(f"/api/verifications/{identifier}").json()["issue_text"]
            == SUBMISSION["issue_text"]
        )


def test_pagination(authenticated_client):
    ids = {create(authenticated_client) for _ in range(3)}
    first = authenticated_client.get("/api/verifications?limit=2").json()
    assert first["has_more"] and first["next_offset"] == 2
    second = authenticated_client.get("/api/verifications?limit=2&offset=2").json()
    assert not second["has_more"]
    assert {i["id"] for i in first["items"] + second["items"]} == ids


def test_intake_and_export_never_claim_tests_passed(
    authenticated_client, github_bundle, monkeypatch
):
    monkeypatch.setattr(verifications, "GitHubClient", lambda token: github_bundle)
    identifier = create(authenticated_client)
    response = authenticated_client.post(f"/api/verifications/{identifier}/intake", headers=HEADERS)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["status"] == "ready"
    assert report["title"] == "Fix expired sessions"
    assert report["snapshot"]["head_sha"] == "b" * 40
    assert all(c["status"] != "pass" for c in report["checks"])
    assert (
        next(c for c in report["checks"] if c["name"] == "test_integrity")["status"]
        == "needs_review"
    )
    assert len(report["attempts"]) == 1
    assert report["attempts"][0]["status"] == "ready"
    exported = authenticated_client.get(f"/api/verifications/{identifier}/export")
    assert exported.json() == report
    assert exported.headers["cache-control"] == "no-store"
    assert "test-token" not in exported.text
    count = len(github_bundle.calls)
    assert (
        authenticated_client.post(f"/api/verifications/{identifier}/intake", headers=HEADERS).json()
        == report
    )
    assert len(github_bundle.calls) == count


def test_failed_intake_retains_failure_and_can_retry(
    authenticated_client, github_bundle, monkeypatch
):
    monkeypatch.setattr(verifications, "GitHubClient", lambda token: github_bundle)
    github_bundle.failure = GitHubFailure("github_timeout", "GitHub timed out.", 504)
    identifier = create(authenticated_client)
    result = authenticated_client.post(
        f"/api/verifications/{identifier}/intake", headers=HEADERS
    ).json()
    assert result["status"] == "failed" and result["snapshot"] is None
    assert result["failure"]["code"] == "github_timeout"
    github_bundle.failure = None
    result = authenticated_client.post(
        f"/api/verifications/{identifier}/intake", headers=HEADERS
    ).json()
    assert result["status"] == "ready" and len(result["attempts"]) == 2


def test_concurrent_claims_and_crash_recovery(authenticated_client):
    identifier = create(authenticated_client)
    store = VerificationStore(app.state.database)

    def claim():
        try:
            return store.claim(identifier, 123)[1]
        except StoreConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim(), range(2)))
    assert sum(token is not None for token in claims) == 1
    old_claim = next(token for token in claims if token)
    with Session(app.state.database.engine) as db:
        db.execute(
            update(Verification)
            .where(Verification.id == identifier)
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )
        db.commit()
    item, new_claim = store.claim(identifier, 123)
    assert item.attempts[0]["status"] == "interrupted"
    with pytest.raises(StoreConflict):
        store.finish(identifier, old_claim, failure={"code": "stale"})
    store.finish(
        identifier, new_claim, failure={"code": "test", "message": "Expected test failure"}
    )


@pytest.mark.parametrize(
    "change",
    [
        {"pr_url": "https://evil.test/o/r/pull/1"},
        {"issue_text": "          "},
        {"issue_text": "x" * 20001},
        {"extra": "not allowed"},
    ],
)
def test_submission_validation(authenticated_client, change):
    assert (
        authenticated_client.post(
            "/api/verifications", json=SUBMISSION | change, headers=HEADERS
        ).status_code
        == 422
    )


def test_oversized_body_rejected_before_parsing(authenticated_client):
    response = authenticated_client.post(
        "/api/verifications",
        content=b"x" * 150000,
        headers=HEADERS | {"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_oversized_chunked_body_rejected(authenticated_client):
    response = authenticated_client.post(
        "/api/verifications",
        content=iter([b"x" * 70000, b"x" * 70000]),
        headers=HEADERS | {"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_empty_completion_cannot_mark_a_verification_ready(authenticated_client):
    identifier = create(authenticated_client)
    store = VerificationStore(app.state.database)
    _, claim = store.claim(identifier, 123)
    with pytest.raises(ValueError, match="captured evidence"):
        store.finish(identifier, claim)
    item = store.get(identifier, 123)
    assert item.status == "intaking" and item.snapshot is None
    completed = store.finish(identifier, claim, failure={"code": "no_evidence"})
    assert completed.status == "failed"
