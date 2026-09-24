"""Public GitHub OAuth integration with process-local, server-side sessions."""

import base64
import hashlib
import os
import secrets
import time
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from codehound.repositories.github_client import GitHubClient, GitHubFailure
from codehound.repositories.urls import parse_pull_url

router = APIRouter()
SESSION_COOKIE = "codehound_session"
FLOW_COOKIE = "codehound_oauth"
SESSION_TTL = 8 * 60 * 60
FLOW_TTL = 10 * 60
# Development only: use a shared, expiring encrypted store before multi-worker deployment.
sessions: dict[str, dict] = {}
flows: dict[str, dict] = {}


def settings():
    origin = os.getenv("CODEHOUND_FRONTEND_URL", "http://localhost:5173").rstrip("/")
    return {
        "origin": origin,
        "callback": f"{origin}/api/auth/github/callback",
        "client_id": os.getenv("GITHUB_CLIENT_ID", ""),
        "client_secret": os.getenv("GITHUB_CLIENT_SECRET", ""),
        "secure": origin.startswith("https://"),
    }


def prune():
    now = time.time()
    for store in (sessions, flows):
        for key in list(store):
            entry = store.get(key)
            if entry and entry["expires"] <= now:
                store.pop(key, None)


def cookie(response, name, value, age):
    response.set_cookie(
        name,
        value,
        max_age=age,
        httponly=True,
        secure=settings()["secure"],
        samesite="lax",
        path="/",
    )


def session(request: Request):
    prune()
    value = sessions.get(request.cookies.get(SESSION_COOKIE, ""))
    if not value:
        raise HTTPException(401, "Sign in with GitHub to continue.")
    return value


def client(token=None):
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(headers=headers, timeout=15, follow_redirects=False)


async def github_get(request: Request, path: str, params=None):
    login = session(request)
    try:
        async with client(login["token"]) as http:
            provider = GitHubClient(login["token"], http_client=http)
            return await provider.json(path, params=params, allow_list=True)
    except GitHubFailure as exc:
        if exc.status == 401:
            sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
        raise HTTPException(exc.status, exc.message) from exc


@router.get("/auth/session")
def get_session(request: Request):
    prune()
    config = settings()
    login = sessions.get(request.cookies.get(SESSION_COOKIE, ""))
    return {
        "configured": bool(config["client_id"] and config["client_secret"]),
        "user": login["user"] if login else None,
    }


def frontend_return(config, verification=None, **parameters):
    # Only an opaque UUID survives OAuth. Never accept an arbitrary redirect URL.
    if verification:
        parameters["verification"] = str(UUID(verification))
    return f"{config['origin']}/?{urlencode(parameters)}"


@router.get("/auth/github/login")
def login(verification: UUID | None = None):
    config = settings()
    destination = str(verification) if verification else None
    if not config["client_id"] or not config["client_secret"]:
        return RedirectResponse(
            frontend_return(config, destination, auth_error="not_configured"), status_code=303
        )
    prune()
    if len(flows) >= 1000:
        raise HTTPException(503, "Too many pending sign-ins. Try again later.")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    flows[state] = {
        "verifier": verifier,
        "expires": time.time() + FLOW_TTL,
        "verification": destination,
    }
    query = urlencode(
        {
            "client_id": config["client_id"],
            "redirect_uri": config["callback"],
            "scope": "",
            "state": state,
            "code_challenge": challenge.decode(),
            "code_challenge_method": "S256",
        }
    )
    response = RedirectResponse(f"https://github.com/login/oauth/authorize?{query}")
    cookie(response, FLOW_COOKIE, state, FLOW_TTL)
    return response


@router.get("/auth/github/callback")
async def callback(request: Request, state: str = "", code: str = "", error: str = ""):
    config = settings()
    prune()
    browser_state = request.cookies.get(FLOW_COOKIE, "")
    if (
        not state.isascii()
        or not browser_state.isascii()
        or not state
        or not browser_state
        or not secrets.compare_digest(state, browser_state)
    ):
        return RedirectResponse(f"{config['origin']}/?auth_error=invalid_state", status_code=303)
    flow = flows.pop(state, None)
    if not flow:
        return RedirectResponse(f"{config['origin']}/?auth_error=expired", status_code=303)

    def failed(reason):
        result = RedirectResponse(
            frontend_return(config, flow.get("verification"), auth_error=reason), status_code=303
        )
        result.delete_cookie(FLOW_COOKIE, path="/")
        return result

    if error or not code:
        return failed("denied")
    try:
        async with client() as http:
            response = await http.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": config["client_id"],
                    "client_secret": config["client_secret"],
                    "code": code,
                    "redirect_uri": config["callback"],
                    "code_verifier": flow["verifier"],
                },
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return failed("exchange_failed")
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            return failed("exchange_failed")
        async with client(token) as http:
            response = await http.get("https://api.github.com/user")
        response.raise_for_status()
        profile = response.json()
        if (
            not isinstance(profile, dict)
            or type(profile.get("id")) is not int
            or profile["id"] <= 0
        ):
            return failed("exchange_failed")
        user = {
            "id": profile["id"],
            "login": profile["login"],
            "name": profile.get("name") or profile["login"],
        }
        lifetime = min(SESSION_TTL, int(payload.get("expires_in", SESSION_TTL)))
        if lifetime <= 0:
            return failed("exchange_failed")
    except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError):
        return failed("exchange_failed")
    prune()
    if len(sessions) >= 10000:
        return failed("unavailable")
    sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
    session_id = secrets.token_urlsafe(32)
    sessions[session_id] = {"token": token, "user": user, "expires": time.time() + lifetime}
    result = RedirectResponse(
        frontend_return(config, flow.get("verification"), github="connected"), status_code=303
    )
    cookie(result, SESSION_COOKIE, session_id, lifetime)
    result.delete_cookie(FLOW_COOKIE, path="/")
    return result


@router.post("/auth/logout")
def logout(request: Request):
    # A same-origin custom header prevents cross-site form submissions; CORS is not enabled.
    if request.headers.get("X-CodeHound-Request") != "1":
        raise HTTPException(403, "Invalid sign-out request.")
    origin = request.headers.get("origin")
    if origin and origin != settings()["origin"]:
        raise HTTPException(403, "Invalid sign-out origin.")
    sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/github/repositories")
async def repositories(request: Request, page: int = Query(1, ge=1, le=10000)):
    data = await github_get(
        request,
        "/user/repos",
        {
            "visibility": "public",
            "affiliation": "owner,collaborator,organization_member",
            "sort": "updated",
            "per_page": 30,
            "page": page,
        },
    )
    if not isinstance(data, list) or any(
        not isinstance(repo, dict) or "id" not in repo or "full_name" not in repo for repo in data
    ):
        raise HTTPException(502, "GitHub returned malformed repository metadata.")
    return {
        "repositories": [
            {
                "id": repo["id"],
                "full_name": repo["full_name"],
                "description": repo.get("description"),
                "language": repo.get("language"),
                "url": f"https://github.com/{repo['full_name']}",
            }
            for repo in data
            if not repo.get("private", True)
        ],
        "has_more": len(data) == 30,
    }


@router.get("/github/repositories/{owner}/{repo}/pulls")
async def pulls(request: Request, owner: str, repo: str, page: int = Query(1, ge=1, le=10000)):
    try:
        parse_pull_url(f"https://github.com/{owner}/{repo}/pull/1")
    except ValueError as exc:
        raise HTTPException(400, "Invalid repository name.") from exc
    metadata = await github_get(request, f"/repos/{owner}/{repo}")
    if not isinstance(metadata, dict):
        raise HTTPException(502, "GitHub returned malformed repository metadata.")
    if metadata.get("private", True):
        raise HTTPException(403, "Only public repositories are supported.")
    data = await github_get(
        request,
        f"/repos/{owner}/{repo}/pulls",
        {
            "state": "open",
            "sort": "updated",
            "direction": "desc",
            "per_page": 30,
            "page": page,
        },
    )
    if not isinstance(data, list) or any(
        not isinstance(pr, dict) or "number" not in pr or "title" not in pr for pr in data
    ):
        raise HTTPException(502, "GitHub returned malformed pull request metadata.")
    return {
        "pulls": [
            {
                "number": pr["number"],
                "title": pr["title"],
                "body": pr.get("body") or "",
                "url": f"https://github.com/{owner}/{repo}/pull/{pr['number']}",
            }
            for pr in data
        ],
        "has_more": len(data) == 30,
    }
