import asyncio
import copy
import hashlib

import httpx
import pytest

from codehound.repositories.github_client import GitHubClient, GitHubFailure
from codehound.repositories.intake import collect_snapshot
from codehound.repositories.urls import parse_pull_url

URL = "https://github.com/octocat/project/pull/7"


def collect(provider):
    return asyncio.run(collect_snapshot(parse_pull_url(URL), provider))


def test_snapshot_pins_merge_base_head_and_hash(github_bundle):
    snapshot = collect(github_bundle)
    assert snapshot["base_target_sha"] == "a" * 40
    assert snapshot["base_sha"] == "c" * 40
    assert snapshot["head_sha"] == "b" * 40
    assert snapshot["diff_sha256"] == hashlib.sha256(snapshot["diff"].encode()).hexdigest()
    assert snapshot["observations"][0]["kind"] == "test_file_changed"
    assert all("token" not in key for key in snapshot)
    assert all(
        "a" * 40 + "..." + "b" * 40 in path for path in github_bundle.calls if "/compare/" in path
    )


@pytest.mark.parametrize(
    "target,value,code",
    [
        ("private", True, "private_repository"),
        ("state", "closed", "unsupported_pr_state"),
        ("changed_files", 301, "too_many_files"),
    ],
)
def test_unsupported_inputs_fail_explicitly(github_bundle, target, value, code):
    (github_bundle.repo if target == "private" else github_bundle.pull)[target] = value
    with pytest.raises(GitHubFailure) as failure:
        collect(github_bundle)
    assert failure.value.code == code


def test_head_update_during_capture_is_rejected(github_bundle):
    github_bundle.after = copy.deepcopy(github_bundle.pull)
    github_bundle.after["head"]["sha"] = "e" * 40
    with pytest.raises(GitHubFailure, match="pr_changed"):
        collect(github_bundle)


def test_changed_title_is_not_mixed_with_old_snapshot(github_bundle):
    github_bundle.after = copy.deepcopy(github_bundle.pull)
    github_bundle.after["title"] = "New task"
    with pytest.raises(GitHubFailure, match="pr_changed"):
        collect(github_bundle)


def test_truncated_inventory_is_rejected(github_bundle):
    github_bundle.comparison["files"] = []
    with pytest.raises(GitHubFailure, match="incomplete_file_list"):
        collect(github_bundle)


def test_malformed_patch_is_rejected(github_bundle):
    github_bundle.patch = "upstream error page"
    with pytest.raises(GitHubFailure, match="incomplete_diff"):
        collect(github_bundle)


def test_partial_diff_with_valid_header_is_rejected(github_bundle):
    missing = copy.deepcopy(github_bundle.comparison["files"][0])
    missing["filename"] = "src/auth.py"
    github_bundle.comparison["files"].append(missing)
    github_bundle.pull.update(changed_files=2, additions=2, deletions=2)
    with pytest.raises(GitHubFailure, match="incomplete_diff"):
        collect(github_bundle)


def test_deleted_fork_is_rejected(github_bundle):
    github_bundle.pull["head"]["repo"] = None
    with pytest.raises(GitHubFailure, match="unavailable_head"):
        collect(github_bundle)


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/o/r/pull/1",
        "https://github.com.evil.test/o/r/pull/1",
        "https://evil@github.com/o/r/pull/1",
        "https://github.com:8443/o/r/pull/1",
        "https://github.com/o/../pull/1",
        "https://github.com/o/r/pull/0",
        "https://github.com/o/r/pull/1?token=secret",
        "https://github.com/o/r/pull/1#files",
        "https://github.com/o/r/pull/1\nextra",
        "https://github.com/o/r/pull/%31",
        "https://github.com/o/r/pull/999999999999999999999999",
        "file:///etc/passwd",
    ],
)
def test_untrusted_url_rejected(url):
    with pytest.raises(ValueError):
        parse_pull_url(url)


def test_canonical_url():
    assert (
        parse_pull_url("  https://github.com/Octocat/my.repo/pull/7/  ").url
        == "https://github.com/Octocat/my.repo/pull/7"
    )


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "session_expired"),
        (403, "github_restricted"),
        (429, "github_restricted"),
        (404, "not_found"),
        (302, "repository_moved"),
        (500, "github_error"),
    ],
)
def test_upstream_failures_sanitized(status, code):
    async def run():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(status, text="secret upstream body")
        )
        async with GitHubClient("secret-token", transport=transport) as client:
            with pytest.raises(GitHubFailure) as failure:
                await client.json("/repos/o/r")
            assert failure.value.code == code
            assert "secret" not in str(failure.value)

    asyncio.run(run())


def test_response_size_limit():
    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 50))
        async with GitHubClient(None, transport=transport) as client:
            with pytest.raises(GitHubFailure, match="evidence_too_large"):
                await client.read("/repos/o/r", limit=10)

    asyncio.run(run())


def test_invalid_json_shape():
    async def run():
        async with GitHubClient(
            None, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
        ) as client:
            with pytest.raises(GitHubFailure, match="invalid_github_response"):
                await client.json("/repos/o/r")

    asyncio.run(run())
