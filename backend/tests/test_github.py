import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from codehound.api import github
from codehound.main import app


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    github.sessions.clear()
    github.flows.clear()
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test-client")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("CODEHOUND_FRONTEND_URL", "http://localhost:5173")
    yield
    github.sessions.clear()
    github.flows.clear()


def signed_in(client):
    github.sessions["session"] = {
        "token": "server-only-token",
        "user": {"login": "octocat", "name": "Octocat"},
        "expires": time.time() + 100,
    }
    client.cookies.set(github.SESSION_COOKIE, "session")


def test_unconfigured_and_anonymous(monkeypatch):
    monkeypatch.delenv("GITHUB_CLIENT_SECRET")
    with TestClient(app) as http:
        response = http.get("/api/auth/session")
        assert response.json() == {"configured": False, "user": None}
        assert response.headers["cache-control"] == "no-store"
        assert http.get("/api/github/repositories").status_code == 401
        assert (
            "not_configured"
            in http.get("/api/auth/github/login", follow_redirects=False).headers["location"]
        )


def test_state_binding_and_pkce():
    with TestClient(app) as http:
        response = http.get("/api/auth/github/login", follow_redirects=False)
        params = parse_qs(urlparse(response.headers["location"]).query)
        assert params["code_challenge_method"] == ["S256"]
        assert len(params["code_challenge"][0]) == 43
        assert "httponly" in response.headers["set-cookie"].lower()
        assert "test-secret" not in response.headers["location"]
        state = params["state"][0]
        result = http.get("/api/auth/github/callback?state=wrong&code=x", follow_redirects=False)
        assert "invalid_state" in result.headers["location"]
        assert state in github.flows
        # Correct state without the browser's bound cookie must also fail.
        http.cookies.clear()
        result = http.get(f"/api/auth/github/callback?state={state}&code=x", follow_redirects=False)
        assert "invalid_state" in result.headers["location"]


def test_callback_session_replay_and_logout(monkeypatch):
    real_client = httpx.AsyncClient

    def handler(request):
        if request.url.path == "/login/oauth/access_token":
            assert b"code_verifier=" in request.content
            return httpx.Response(200, json={"access_token": "private-token"})
        assert request.headers["Authorization"] == "Bearer private-token"
        return httpx.Response(200, json={"login": "octocat", "name": "Octocat"})

    monkeypatch.setattr(
        github,
        "client",
        lambda token=None: real_client(
            transport=httpx.MockTransport(handler),
            headers={"Authorization": f"Bearer {token}"} if token else {},
        ),
    )
    with TestClient(app) as http:
        initial = http.get("/api/auth/github/login", follow_redirects=False)
        state = parse_qs(urlparse(initial.headers["location"]).query)["state"][0]
        callback_url = f"/api/auth/github/callback?state={state}&code=example"
        response = http.get(callback_url, follow_redirects=False)
        assert response.headers["location"] == "http://localhost:5173/?github=connected"
        assert "private-token" not in str(response.headers)
        assert "private-token" not in http.get("/api/auth/session").text
        assert http.get("/api/auth/session").json()["user"]["login"] == "octocat"
        assert "invalid_state" in http.get(callback_url, follow_redirects=False).headers["location"]
        assert http.post("/api/auth/logout").status_code == 403
        assert (
            http.post(
                "/api/auth/logout",
                headers={
                    "X-CodeHound-Request": "1",
                    "Origin": "https://evil.example",
                },
            ).status_code
            == 403
        )
        assert (
            http.post("/api/auth/logout", headers={"X-CodeHound-Request": "1"}).status_code == 200
        )
        assert http.get("/api/auth/session").json()["user"] is None
        assert not github.sessions


def test_repository_pagination_and_private_filter(monkeypatch):
    async def fake_get(request, path, params=None):
        github.session(request)
        assert path == "/user/repos"
        assert params["visibility"] == "public"
        assert params["page"] == 2
        return [
            {"id": 1, "full_name": "octocat/public", "private": False},
            {"id": 2, "full_name": "octocat/private", "private": True},
        ]

    monkeypatch.setattr(github, "github_get", fake_get)
    with TestClient(app) as http:
        signed_in(http)
        response = http.get("/api/github/repositories?page=2")
        assert [r["full_name"] for r in response.json()["repositories"]] == ["octocat/public"]
        assert response.json()["has_more"] is False
        assert http.get("/api/github/repositories?page=0").status_code == 422


def test_expired_session():
    with TestClient(app) as http:
        signed_in(http)
        github.sessions["session"]["expires"] = 0
        assert http.get("/api/github/repositories").status_code == 401
        assert not github.sessions


def test_private_pull_requests_rejected(monkeypatch):
    async def fake_get(request, path, params=None):
        github.session(request)
        return {"private": True}

    monkeypatch.setattr(github, "github_get", fake_get)
    with TestClient(app) as http:
        signed_in(http)
        assert http.get("/api/github/repositories/octocat/private/pulls").status_code == 403


def test_secure_cookie_on_https(monkeypatch):
    monkeypatch.setenv("CODEHOUND_FRONTEND_URL", "https://codehound.example")
    with TestClient(app) as http:
        response = http.get("/api/auth/github/login", follow_redirects=False)
        assert "secure" in response.headers["set-cookie"].lower()


def test_github_auth_failure_clears_session(monkeypatch):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        github,
        "client",
        lambda token=None: real_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(401)),
        ),
    )
    with TestClient(app) as http:
        signed_in(http)
        assert http.get("/api/github/repositories").status_code == 401
        assert not github.sessions


@pytest.mark.parametrize("reason", ["denied", "expired"])
def test_canceled_and_expired_flow(reason):
    with TestClient(app) as http:
        response = http.get("/api/auth/github/login", follow_redirects=False)
        state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
        if reason == "expired":
            github.flows[state]["expires"] = 0
        result = http.get(
            f"/api/auth/github/callback?state={state}&error=access_denied", follow_redirects=False
        )
        assert f"auth_error={reason}" in result.headers["location"]
        assert not github.sessions
        assert state not in github.flows


def test_public_pr_selection_data(monkeypatch):
    async def fake_get(request, path, params=None):
        github.session(request)
        if path.endswith("/pulls"):
            assert params["state"] == "open"
            assert params["page"] == 2
            return [{"number": 7, "title": "Fix session", "body": "Acceptance criteria"}]
        return {"private": False}

    monkeypatch.setattr(github, "github_get", fake_get)
    with TestClient(app) as http:
        signed_in(http)
        response = http.get("/api/github/repositories/octocat/public/pulls?page=2")
        assert response.json() == {
            "pulls": [
                {
                    "number": 7,
                    "title": "Fix session",
                    "body": "Acceptance criteria",
                    "url": "https://github.com/octocat/public/pull/7",
                }
            ],
            "has_more": False,
        }
