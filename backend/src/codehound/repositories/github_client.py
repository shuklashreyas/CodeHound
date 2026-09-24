"""Bounded read-only requests to GitHub's fixed API origin."""

import json
from dataclasses import dataclass

import httpx


@dataclass
class GitHubFailure(Exception):
    code: str
    message: str
    status: int = 502


class GitHubClient:
    def __init__(self, token: str | None, transport=None, http_client=None):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = http_client or httpx.AsyncClient(
            base_url="https://api.github.com",
            headers=headers,
            timeout=httpx.Timeout(15, connect=5),
            follow_redirects=False,
            transport=transport,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.http.aclose()

    async def read(self, path: str, *, params=None, accept=None, limit=8 * 1024 * 1024) -> bytes:
        if (
            not (path.startswith("/repos/") or path in ("/user", "/user/repos"))
            or "?" in path
            or "#" in path
            or ".." in path.split("/")
        ):
            raise GitHubFailure("invalid_path", "Invalid GitHub resource path.", 400)
        headers = {"Accept": accept} if accept else None
        try:
            async with self.http.stream(
                "GET", f"https://api.github.com{path}", params=params, headers=headers
            ) as response:
                if response.status_code == 401:
                    raise GitHubFailure("session_expired", "Sign in with GitHub again.", 401)
                if response.status_code in (403, 429):
                    raise GitHubFailure(
                        "github_restricted", "GitHub access is restricted or rate limited.", 503
                    )
                if response.status_code == 404:
                    raise GitHubFailure(
                        "not_found", "The public repository or PR is unavailable.", 404
                    )
                if response.status_code in (301, 302, 307, 308):
                    raise GitHubFailure(
                        "repository_moved", "The repository moved. Submit its current URL.", 409
                    )
                if response.status_code != 200:
                    raise GitHubFailure("github_error", "GitHub could not provide this evidence.")
                parts = []
                total = 0
                async for part in response.aiter_bytes():
                    total += len(part)
                    if total > limit:
                        raise GitHubFailure(
                            "evidence_too_large",
                            "GitHub evidence exceeds the configured size limit.",
                            413,
                        )
                    parts.append(part)
                return b"".join(parts)
        except httpx.TimeoutException as exc:
            raise GitHubFailure(
                "github_timeout", "GitHub timed out. Retry intake later.", 504
            ) from exc
        except httpx.RequestError as exc:
            raise GitHubFailure("github_unavailable", "GitHub could not be reached.", 502) from exc

    async def json(self, path: str, *, params=None, allow_list=False):
        raw = await self.read(path, params=params)
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise GitHubFailure("invalid_github_response", "GitHub returned invalid JSON.") from exc
        if not isinstance(data, (dict, list) if allow_list else dict):
            raise GitHubFailure(
                "invalid_github_response", "GitHub returned an unexpected response shape."
            )
        return data

    async def diff(self, path: str):
        raw = await self.read(path, accept="application/vnd.github.diff", limit=4 * 1024 * 1024)
        try:
            return raw.decode("utf-8")
        except UnicodeError as exc:
            raise GitHubFailure("unsupported_diff", "The diff is not valid UTF-8.") from exc
