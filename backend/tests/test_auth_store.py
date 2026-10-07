import os
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.schema import CreateSchema, DropSchema

from codehound.api import github
from codehound.db.auth import SharedAuthStore, lookup_id
from codehound.db.database import Database
from codehound.db.models import AuthRecord
from codehound.main import app


def flow(**changes):
    return {"verifier": "v" * 64, "verification": None, "expires": time.time() + 300} | changes


def login(**changes):
    return {
        "token": "test-auth-secret-token",
        "user": {"id": 123, "login": "test", "name": "Test"},
        "expires": time.time() + 600,
    } | changes


@pytest.fixture
def shared(tmp_path):
    url = f"sqlite:///{tmp_path / 'shared-auth.db'}"
    first, second = Database(url), Database(url)
    key = Fernet.generate_key()
    try:
        first.migrate()
        yield SharedAuthStore(first, key), SharedAuthStore(second, key)
    finally:
        first.close()
        second.close()


def test_cross_instance_session_read_logout_and_encrypted_storage(shared):
    first, second = shared
    identifier = secrets.token_urlsafe(32)
    payload = login()
    assert first.save_session(identifier, payload)
    assert second.get_session(identifier) == payload
    with second.database.engine.connect() as connection:
        row = connection.execute(select(AuthRecord.__table__)).mappings().one()
    assert row["id_hash"] == lookup_id(identifier)
    assert row["id_hash"] != identifier
    assert identifier not in str(dict(row))
    assert payload["token"] not in str(dict(row))
    assert payload["user"]["name"] not in row["ciphertext"]
    second.revoke_session(identifier)
    assert first.get_session(identifier) is None


def test_flow_is_single_use_across_concurrent_instances(shared):
    first, second = shared
    identifier = secrets.token_urlsafe(32)
    payload = flow()
    assert first.save_flow(identifier, payload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda store: store.consume_flow(identifier), [first, second]))
    assert values.count(payload) == 1
    assert values.count(None) == 1
    assert first.consume_flow(identifier) is None


def test_expiry_enforced_from_authenticated_payload_not_just_database(shared, monkeypatch):
    first, second = shared
    now = time.time()
    session_id, state = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    assert first.save_session(session_id, login(expires=now + 10))
    assert first.save_flow(state, flow(expires=now + 10))
    with first.database.engine.begin() as connection:
        connection.execute(update(AuthRecord).values(expires_ms=int((now + 10000) * 1000)))
    monkeypatch.setattr("codehound.db.auth.time.time", lambda: now + 11)
    assert second.get_session(session_id) is None
    assert second.consume_flow(state) is None
    assert first.consume_flow(state) is None


def test_ciphertext_cannot_be_swapped_between_ids_or_kinds(shared):
    first, second = shared
    one, two, state = [secrets.token_urlsafe(32) for _ in range(3)]
    assert first.save_session(one, login())
    assert first.save_session(two, login(token="other-test-token"))
    assert first.save_flow(state, flow())
    with first.database.engine.begin() as connection:
        cipher = connection.execute(
            select(AuthRecord.ciphertext).where(AuthRecord.id_hash == lookup_id(one))
        ).scalar_one()
        connection.execute(
            update(AuthRecord)
            .where(AuthRecord.id_hash.in_([lookup_id(two), lookup_id(state)]))
            .values(ciphertext=cipher)
        )
    assert second.get_session(two) is None
    assert second.consume_flow(state) is None
    assert second.get_session(one)["token"] == "test-auth-secret-token"


def test_wrong_or_malformed_keys_fail_closed(shared):
    first, _ = shared
    with pytest.raises(RuntimeError, match="does not match"):
        SharedAuthStore(first.database, Fernet.generate_key())
    for key in ("", "not-a-key", "☃", None):
        with pytest.raises(RuntimeError, match="valid Fernet key"):
            SharedAuthStore(first.database, key)


def test_tampered_cipher_and_bad_identifiers_cannot_authenticate(shared):
    first, second = shared
    identifier = secrets.token_urlsafe(32)
    assert first.save_session(identifier, login())
    with first.database.engine.begin() as connection:
        connection.execute(update(AuthRecord).values(ciphertext="tampered"))
    assert second.get_session(identifier) is None
    for identifier in ("☃", "x" * 257, "../escape", "", None):
        assert second.get_session(identifier) is None
        assert second.consume_flow(identifier) is None


def test_bounds_capacity_and_reused_ids(shared, monkeypatch):
    first, second = shared
    monkeypatch.setitem(__import__("codehound.db.auth", fromlist=["CAPACITY"]).CAPACITY, "flow", 1)
    state = secrets.token_urlsafe(32)
    assert first.save_flow(state, flow())
    assert not second.save_flow(secrets.token_urlsafe(32), flow())
    assert not first.save_session(secrets.token_urlsafe(32), login(token="x" * 17000))
    assert not first.save_session(secrets.token_urlsafe(32), login(expires=float("inf")))
    assert not first.save_session(secrets.token_urlsafe(32), login(expires=10**1000))
    assert not first.save_flow(secrets.token_urlsafe(32), flow(expires=time.time() + 10000))
    identifier = secrets.token_urlsafe(32)
    assert first.save_session(identifier, login())
    with pytest.raises(ValueError, match="already in use"):
        second.save_session(identifier, login(token="replacement"))
    assert second.get_session(identifier)["token"] == "test-auth-secret-token"


def test_api_oauth_cross_instance_session_and_replay(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CODEHOUND_SESSION_ENCRYPTION_KEY", key)
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test-client")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("CODEHOUND_FRONTEND_URL", "http://localhost:5173")
    real_client = httpx.AsyncClient

    def handler(request):
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "test-shared-private-token"})
        return httpx.Response(200, json={"id": 123, "login": "test", "name": "Test"})

    monkeypatch.setattr(
        github, "client", lambda token=None: real_client(transport=httpx.MockTransport(handler))
    )
    with TestClient(app) as first:
        initial = first.get("/api/auth/github/login", follow_redirects=False)
        state = parse_qs(urlparse(initial.headers["location"]).query)["state"][0]
        callback = f"/api/auth/github/callback?state={state}&code=test-code"
        # Recreate the shared store as if callback landed on another worker.
        app.state.auth_store = SharedAuthStore(app.state.database, key)
        result = first.get(callback, follow_redirects=False)
        assert "github=connected" in result.headers["location"]
        session_id = first.cookies.get(github.SESSION_COOKIE)
        first.cookies.set(github.FLOW_COOKIE, state)
        assert (
            "auth_error=expired" in first.get(callback, follow_redirects=False).headers["location"]
        )
    with TestClient(app) as reopened:
        reopened.cookies.set(github.SESSION_COOKIE, session_id)
        assert reopened.get("/api/auth/session").json()["user"]["id"] == 123
        assert "test-shared-private-token" not in reopened.get("/api/auth/session").text
        assert (
            reopened.post("/api/auth/logout", headers={"X-CodeHound-Request": "1"}).status_code
            == 200
        )
        assert reopened.get("/api/auth/session").json()["user"] is None


def test_opted_shared_missing_key_never_uses_memory(monkeypatch):
    monkeypatch.setenv("CODEHOUND_SESSION_ENCRYPTION_KEY", "")
    with pytest.raises(RuntimeError, match="valid Fernet key"):
        with TestClient(app):
            pass


def test_shared_database_failure_never_falls_back_to_memory(monkeypatch):
    monkeypatch.setenv("CODEHOUND_SESSION_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with TestClient(app) as client:
        identifier = secrets.token_urlsafe(32)
        github.sessions[identifier] = login()
        client.cookies.set(github.SESSION_COOKIE, identifier)

        def unavailable(identifier):
            raise OperationalError("test query", {"sensitive": "fake-private-token"}, Exception())

        monkeypatch.setattr(app.state.auth_store, "get_session", unavailable)
        response = client.get("/api/auth/session")
        assert response.status_code == 503
        assert "fake-private-token" not in response.text
        github.sessions.pop(identifier, None)


def test_shared_provider_401_revokes_database_record(monkeypatch):
    monkeypatch.setenv("CODEHOUND_SESSION_ENCRYPTION_KEY", Fernet.generate_key().decode())
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        github,
        "client",
        lambda token=None: real_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(401))
        ),
    )
    with TestClient(app) as client:
        identifier = secrets.token_urlsafe(32)
        store = app.state.auth_store
        assert store.save_session(identifier, login())
        client.cookies.set(github.SESSION_COOKIE, identifier)
        assert client.get("/api/github/repositories").status_code == 401
        assert store.get_session(identifier) is None


@pytest.mark.skipif(
    not os.getenv("CODEHOUND_TEST_POSTGRES_URL"), reason="Isolated PostgreSQL required"
)
def test_postgresql_shared_auth_atomic_flow_and_revocation():
    base_url = make_url(os.environ["CODEHOUND_TEST_POSTGRES_URL"])
    # Every run owns a separate schema; other agents' PostgreSQL tests are unaffected.
    schema = "auth_test_" + secrets.token_hex(12)
    bootstrap = Database(base_url)
    try:
        with bootstrap.engine.begin() as connection:
            connection.execute(CreateSchema(schema))
    finally:
        bootstrap.close()
    test_url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
    database, reopened = Database(test_url), Database(test_url)
    key = Fernet.generate_key()
    try:
        database.migrate()
        first, second = SharedAuthStore(database, key), SharedAuthStore(reopened, key)
        state, identifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        assert first.save_flow(state, flow())
        assert first.save_session(identifier, login())
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(lambda store: store.consume_flow(state), [first, second]))
        assert sum(value is not None for value in values) == 1
        assert second.get_session(identifier)["user"]["id"] == 123
        second.revoke_session(identifier)
        assert first.get_session(identifier) is None
    finally:
        with database.engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        database.close()
        reopened.close()
