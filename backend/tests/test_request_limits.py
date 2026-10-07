import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, Header, Request
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from codehound.core.request_limits import (
    remote_identity,
    require_mutation_limit,
    require_oauth_limit,
)
from codehound.db.database import Database
from codehound.db.models import ExecutionNamespace
from codehound.db.rate_limits import (
    PRUNE_BATCH,
    RequestLimitBucket,
    RequestLimitConfiguration,
    RequestLimitSettings,
    RequestLimitStorageFull,
    RequestLimitStore,
    principal_digest,
    rate_limit_settings,
)


@pytest.fixture
def database():
    database = Database()
    database.migrate()
    yield database
    database.close()


@pytest.mark.parametrize(
    "key,maximum",
    [
        ("CODEHOUND_MUTATION_REQUEST_LIMIT", 10000),
        ("CODEHOUND_OAUTH_REQUEST_LIMIT", 1000),
        ("CODEHOUND_REQUEST_LIMIT_WINDOW_SECONDS", 3600),
        ("CODEHOUND_REQUEST_LIMIT_MAX_BUCKETS", 1000000),
    ],
)
@pytest.mark.parametrize("value", ["0", "-1", "", "unlimited", "1.5", " 2", "+2", "01"])
def test_invalid_limit_configuration_fails_closed(monkeypatch, key, maximum, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(RequestLimitConfiguration):
        rate_limit_settings()
    monkeypatch.setenv(key, str(maximum + 1))
    with pytest.raises(RequestLimitConfiguration):
        rate_limit_settings()


def test_defaults_and_constructor_limits_are_bounded(monkeypatch):
    for key in (
        "CODEHOUND_MUTATION_REQUEST_LIMIT",
        "CODEHOUND_OAUTH_REQUEST_LIMIT",
        "CODEHOUND_REQUEST_LIMIT_WINDOW_SECONDS",
        "CODEHOUND_REQUEST_LIMIT_MAX_BUCKETS",
    ):
        monkeypatch.delenv(key, raising=False)
    assert rate_limit_settings() == RequestLimitSettings(120, 20, 60, 100000)
    with pytest.raises(RequestLimitConfiguration):
        RequestLimitSettings(True, 20, 60, 100000)


def test_fixed_window_reset_and_independent_principals(database):
    store = RequestLimitStore(database)
    settings = RequestLimitSettings(2, 1, 60, 20)
    assert store.consume("mutation", "owner:123", settings=settings, now=120).remaining == 1
    assert store.consume("mutation", "owner:123", settings=settings, now=121).allowed
    denied = store.consume("mutation", "owner:123", settings=settings, now=122)
    assert not denied.allowed and denied.retry_after == 58 and denied.remaining == 0
    assert store.consume("mutation", "owner:456", settings=settings, now=122).allowed
    assert store.consume("oauth", "ip:127.0.0.1", settings=settings, now=122).allowed
    assert not store.consume("oauth", "ip:127.0.0.1", settings=settings, now=179).allowed
    assert store.consume("mutation", "owner:123", settings=settings, now=180).allowed
    assert store.consume("oauth", "ip:127.0.0.1", settings=settings, now=180).allowed


def test_counters_shared_between_database_instances_and_salted_hash_only(database):
    settings = RequestLimitSettings(1, 1, 60, 20)
    reopened = Database(str(database.engine.url))
    try:
        first = RequestLimitStore(database).consume(
            "oauth", "ip:198.51.100.12", settings=settings, now=120
        )
        second = RequestLimitStore(reopened).consume(
            "oauth", "ip:198.51.100.12", settings=settings, now=121
        )
        assert first.allowed and not second.allowed
        with Session(database.engine) as db:
            row = db.scalar(select(RequestLimitBucket))
            namespace = db.scalar(select(ExecutionNamespace.value))
            assert row.principal_sha256 == principal_digest(namespace, "oauth", "ip:198.51.100.12")
            assert "198.51.100.12" not in row.principal_sha256
            assert row.requests == 1
            assert not hasattr(row, "identity")
        assert principal_digest("different", "oauth", "ip:198.51.100.12") != row.principal_sha256
    finally:
        reopened.close()


def test_concurrent_sqlite_counter_cannot_exceed_limit(database):
    store = RequestLimitStore(database)
    settings = RequestLimitSettings(4, 1, 60, 20)

    def consume(_):
        return store.consume("mutation", "owner:123", settings=settings, now=120).allowed

    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(consume, range(24))) == 4
    with Session(database.engine) as db:
        assert db.scalar(select(RequestLimitBucket.requests)) == 4


def test_global_bucket_cap_and_expiry_pruning_are_atomic(database):
    store = RequestLimitStore(database)
    settings = RequestLimitSettings(5, 5, 60, 2)

    def consume(owner):
        try:
            return store.consume("mutation", f"owner:{owner}", settings=settings, now=120).allowed
        except RequestLimitStorageFull:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(consume, range(8))) == 2
    with Session(database.engine) as db:
        assert db.scalar(select(func.count()).select_from(RequestLimitBucket)) == 2
    assert store.consume("mutation", "owner:new", settings=settings, now=180).allowed


def test_pruning_batch_is_bounded(database):
    with Session(database.engine) as db:
        db.add_all(
            RequestLimitBucket(
                bucket="mutation",
                principal_sha256=f"{index:064x}",
                window_start=0,
                window_end=60,
                requests=1,
            )
            for index in range(PRUNE_BATCH + 5)
        )
        db.commit()
    RequestLimitStore(database).consume("mutation", "owner:123", now=120)
    with Session(database.engine) as db:
        assert db.scalar(select(func.count()).select_from(RequestLimitBucket)) == 6


def make_client(database, *, host="198.51.100.10"):
    app = FastAPI()
    app.state.database = database

    @app.post("/verification")
    @app.post("/intake")
    @app.post("/execution")
    def mutate(request: Request, owner: int = Header(123)):
        require_mutation_limit(request, owner)
        return {"ok": True}

    @app.get("/oauth")
    def oauth(request: Request):
        require_oauth_limit(request)
        return {"ok": True}

    return TestClient(app, client=(host, 12345))


def test_http_mutations_share_owner_budget_and_idempotent_retries_count(database, monkeypatch):
    monkeypatch.setenv("CODEHOUND_MUTATION_REQUEST_LIMIT", "2")
    key = str(uuid4())
    client = make_client(database)
    assert client.post("/verification", headers={"Idempotency-Key": key}).status_code == 200
    assert client.post("/intake", headers={"Idempotency-Key": key}).status_code == 200
    denied = client.post("/execution", headers={"Idempotency-Key": key})
    assert denied.status_code == 429
    assert 1 <= int(denied.headers["Retry-After"]) <= 60
    assert client.post("/execution", headers={"owner": "456"}).status_code == 200
    assert client.post("/execution", headers={"owner": "0"}).status_code == 401


def test_forwarded_headers_cannot_evade_oauth_peer_budget(database, monkeypatch):
    monkeypatch.setenv("CODEHOUND_OAUTH_REQUEST_LIMIT", "1")
    client = make_client(database)
    assert client.get("/oauth", headers={"X-Forwarded-For": "203.0.113.1"}).status_code == 200
    denied = client.get(
        "/oauth", headers={"X-Forwarded-For": "203.0.113.2", "Forwarded": "for=203.0.113.3"}
    )
    assert denied.status_code == 429 and int(denied.headers["Retry-After"]) >= 1
    assert make_client(database, host="198.51.100.11").get("/oauth").status_code == 200


def test_docker_uvicorn_startup_cannot_rewrite_peer_for_oauth_limit(database, monkeypatch):
    import importlib
    import json

    from click.testing import CliRunner
    from uvicorn import Config

    monkeypatch.setenv("CODEHOUND_OAUTH_REQUEST_LIMIT", "1")
    from codehound.db import rate_limits

    monkeypatch.setattr(rate_limits, "time", SimpleNamespace(time=lambda: 120))
    dockerfile = Path(__file__).parents[1] / "Dockerfile"
    command = next(line for line in dockerfile.read_text().splitlines() if line.startswith("CMD "))
    arguments = json.loads(command.removeprefix("CMD "))
    captured = {}
    uvicorn_cli = importlib.import_module("uvicorn.main")
    monkeypatch.setattr(uvicorn_cli, "run", lambda *args, **kwargs: captured.update(kwargs))
    # Parse the actual checked-in container command through Uvicorn's CLI, then
    # exercise the configured ASGI stack, including any proxy middleware.
    invocation = CliRunner().invoke(uvicorn_cli.main, arguments[1:])
    assert invocation.exit_code == 0, invocation.output
    config = Config(
        make_client(database).app,
        proxy_headers=captured["proxy_headers"],
        forwarded_allow_ips=captured["forwarded_allow_ips"],
    )
    config.load()
    client = TestClient(config.loaded_app, client=("127.0.0.1", 12345))
    assert client.get("/oauth", headers={"X-Forwarded-For": "203.0.113.1"}).status_code == 200
    denied = client.get("/oauth", headers={"X-Forwarded-For": "203.0.113.2"})
    assert denied.status_code == 429 and denied.headers["Retry-After"] == "60"


def test_invalid_configuration_and_bucket_exhaustion_return_503(database, monkeypatch):
    client = make_client(database)
    monkeypatch.setenv("CODEHOUND_OAUTH_REQUEST_LIMIT", "unlimited")
    response = client.get("/oauth")
    assert response.status_code == 503 and "unlimited" not in response.text
    monkeypatch.setenv("CODEHOUND_OAUTH_REQUEST_LIMIT", "20")
    monkeypatch.setenv("CODEHOUND_REQUEST_LIMIT_MAX_BUCKETS", "1")
    assert client.get("/oauth").status_code == 200
    assert client.post("/execution").status_code == 503


def test_database_failure_returns_503_without_leaking_query(database, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OperationalError("private query", {"private": "credential"}, RuntimeError())

    monkeypatch.setattr(RequestLimitStore, "consume", unavailable)
    response = make_client(database).post("/execution")
    assert response.status_code == 503
    assert "credential" not in response.text and "private query" not in response.text


def test_remote_identity_normalizes_ip_and_ignores_header_content():
    for raw, expected in (
        ("2001:db8:0:0:0:0:0:1", "ip:2001:db8::1"),
        ("::ffff:192.0.2.1", "ip:192.0.2.1"),
        ("not-an-ip", "ip:unknown"),
    ):
        request = SimpleNamespace(
            client=SimpleNamespace(host=raw), headers={"X-Forwarded-For": "1.2.3.4"}
        )
        assert remote_identity(request) == expected
    assert remote_identity(SimpleNamespace(client=None)) == "ip:unknown"


def test_real_api_create_intake_execution_and_idempotent_retry_share_budget(
    authenticated_client, github_bundle, monkeypatch, tmp_path
):
    from codehound.api import verifications
    from codehound.db.models import ExecutionJob, Verification
    from codehound.evaluation.registry import load_profiles
    from codehound.main import app

    monkeypatch.setenv("CODEHOUND_MUTATION_REQUEST_LIMIT", "4")
    from codehound.db import rate_limits

    monkeypatch.setattr(rate_limits, "time", SimpleNamespace(time=lambda: 120))
    monkeypatch.setattr(verifications, "GitHubClient", lambda token: github_bundle)
    profile = load_profiles()["codehound-url-contract"].model_copy(
        update={"id": "request-test", "repository": "octocat/project"}
    )
    directory = tmp_path / "profiles"
    directory.mkdir()
    (directory / "profile.json").write_text(profile.model_dump_json())
    monkeypatch.setenv("CODEHOUND_PROFILE_DIR", str(directory))
    monkeypatch.setenv("CODEHOUND_EXECUTION_IMAGE_ID", "sha256:" + "a" * 64)
    headers = {"X-CodeHound-Request": "1"}
    submission = {
        "pr_url": "https://github.com/octocat/project/pull/7",
        "issue_text": "Verify the refresh behavior independently.",
    }
    created = authenticated_client.post("/api/verifications", json=submission, headers=headers)
    assert created.status_code == 201
    identifier = created.json()["id"]
    intake = authenticated_client.post(f"/api/verifications/{identifier}/intake", headers=headers)
    assert intake.status_code == 200 and intake.json()["status"] == "ready"
    path = f"/api/verifications/{identifier}/executions"
    execution_headers = headers | {"Idempotency-Key": str(uuid4())}
    first = authenticated_client.post(
        path, json={"profile_id": profile.id}, headers=execution_headers
    )
    repeated = authenticated_client.post(
        path, json={"profile_id": profile.id}, headers=execution_headers
    )
    assert first.status_code == 202 and repeated.status_code == 200
    assert first.json()["id"] == repeated.json()["id"]
    denied = authenticated_client.post("/api/verifications", json=submission, headers=headers)
    assert denied.status_code == 429 and denied.headers["Retry-After"] == "60"
    with Session(app.state.database.engine) as db:
        assert db.scalar(select(RequestLimitBucket.requests)) == 4
        assert db.scalar(select(func.count()).select_from(Verification)) == 1
        assert db.scalar(select(func.count()).select_from(ExecutionJob)) == 1


def test_real_api_auth_origin_and_body_rejections_do_not_consume_owner_quota(
    authenticated_client, monkeypatch
):
    from codehound.api import github
    from codehound.main import app

    monkeypatch.setenv("CODEHOUND_MUTATION_REQUEST_LIMIT", "1")
    from codehound.db import rate_limits

    monkeypatch.setattr(rate_limits, "time", SimpleNamespace(time=lambda: 120))
    submission = {
        "pr_url": "https://github.com/octocat/project/pull/7",
        "issue_text": "Verify the public behavior.",
    }
    headers = {"X-CodeHound-Request": "1"}
    authenticated_client.cookies.clear()
    assert (
        authenticated_client.post(
            "/api/verifications", json=submission, headers=headers
        ).status_code
        == 401
    )
    authenticated_client.cookies.set(github.SESSION_COOKIE, "test-session")
    assert authenticated_client.post("/api/verifications", json=submission).status_code == 403
    assert (
        authenticated_client.post(
            "/api/verifications",
            json=submission,
            headers=headers | {"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert (
        authenticated_client.post("/api/verifications", json={}, headers=headers).status_code == 422
    )
    with Session(app.state.database.engine) as db:
        assert db.scalar(select(func.count()).select_from(RequestLimitBucket)) == 0
    assert (
        authenticated_client.post(
            "/api/verifications", json=submission, headers=headers
        ).status_code
        == 201
    )
    assert (
        authenticated_client.post(
            "/api/verifications", json=submission, headers=headers
        ).status_code
        == 429
    )
    github.sessions["test-session"]["user"]["id"] = 456
    assert (
        authenticated_client.post(
            "/api/verifications", json=submission, headers=headers
        ).status_code
        == 201
    )


def test_real_oauth_route_ignores_spoofed_forwarding_headers(database, monkeypatch):
    from codehound.db import rate_limits
    from codehound.main import app

    monkeypatch.setenv("CODEHOUND_OAUTH_REQUEST_LIMIT", "1")
    monkeypatch.setattr(rate_limits, "time", SimpleNamespace(time=lambda: 120))
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_CLIENT_SECRET", raising=False)
    with TestClient(app, client=("198.51.100.20", 12345)) as client:
        first = client.get(
            "/api/auth/github/login",
            headers={"X-Forwarded-For": "203.0.113.1"},
            follow_redirects=False,
        )
        assert first.status_code == 303
        denied = client.get(
            "/api/auth/github/login",
            headers={"X-Forwarded-For": "203.0.113.2", "Forwarded": "for=203.0.113.3"},
            follow_redirects=False,
        )
        assert denied.status_code == 429 and denied.headers["Retry-After"] == "60"
        with Session(app.state.database.engine) as db:
            assert db.scalar(select(RequestLimitBucket.requests)) == 1


@pytest.mark.skipif(
    not os.getenv("CODEHOUND_TEST_POSTGRES_URL"), reason="Isolated PostgreSQL not configured"
)
def test_shared_postgres_counter_is_atomic_across_connections():
    database = Database(os.environ["CODEHOUND_TEST_POSTGRES_URL"])
    identity = f"owner:{uuid4()}"
    store = RequestLimitStore(database)
    settings = RequestLimitSettings(3, 1, 60, 100000)
    try:
        database.migrate()

        def consume(_):
            return store.consume("mutation", identity, settings=settings).allowed

        with ThreadPoolExecutor(max_workers=10) as pool:
            assert sum(pool.map(consume, range(20))) == 3
    finally:
        with Session(database.engine) as db:
            namespace = db.scalar(select(ExecutionNamespace.value))
            digest = principal_digest(namespace, "mutation", identity)
            db.execute(
                delete(RequestLimitBucket).where(RequestLimitBucket.principal_sha256 == digest)
            )
            db.commit()
        database.close()


@pytest.mark.skipif(
    not os.getenv("CODEHOUND_TEST_POSTGRES_URL"), reason="Isolated PostgreSQL not configured"
)
def test_postgres_global_bucket_cap_is_atomic_in_isolated_schema():
    base = Database(os.environ["CODEHOUND_TEST_POSTGRES_URL"])
    schema = "codehound_rate_test_" + uuid4().hex
    isolated = None
    try:
        with base.engine.begin() as db:
            db.execute(text(f"CREATE SCHEMA {schema}"))
        url = base.engine.url.set(query={"options": f"-c search_path={schema}"})
        isolated = Database(url)
        isolated.migrate()
        store = RequestLimitStore(isolated)
        settings = RequestLimitSettings(10, 10, 60, 2)

        def consume(owner):
            try:
                return store.consume("mutation", f"owner:{owner}", settings=settings).allowed
            except RequestLimitStorageFull:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            assert sum(pool.map(consume, range(8))) == 2
        with Session(isolated.engine) as db:
            assert db.scalar(select(func.count()).select_from(RequestLimitBucket)) == 2
    finally:
        if isolated:
            isolated.close()
        with base.engine.begin() as db:
            db.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        base.close()
