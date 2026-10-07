import asyncio

import pytest

from codehound.repositories.checkout import CheckoutFailure, GitWorkspace, directory_usage


def test_checkout_rejects_unpinned_refs_and_hostile_urls():
    with pytest.raises(ValueError):
        GitWorkspace("octocat/repo", "main", "b" * 40)
    with pytest.raises(ValueError):
        GitWorkspace("../../etc", "a" * 40, "b" * 40)


def test_checkout_does_not_inherit_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "do-not-inherit")
    monkeypatch.setenv("GIT_SSH_COMMAND", "malicious helper")
    workspace = GitWorkspace("octocat/repo", "a" * 40, "b" * 40)
    workspace.root = tmp_path
    env = workspace.environment()
    assert "GITHUB_CLIENT_SECRET" not in env and "GIT_SSH_COMMAND" not in env
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "protocol.file.allow=never" in workspace.git_args("fetch")
    assert "credential.helper=" in workspace.git_args("fetch")


def test_usage_does_not_follow_symlinks(tmp_path):
    outside = tmp_path / "outside"
    root = tmp_path / "workspace"
    outside.mkdir()
    root.mkdir()
    (outside / "secret").write_bytes(b"x" * 1000)
    (root / "link").symlink_to(outside, target_is_directory=True)
    (root / "small").write_bytes(b"ok")
    assert directory_usage(root) == (2, 1)


def test_checkout_cleanup_on_fetch_failure(monkeypatch, tmp_path):
    workspace = GitWorkspace("octocat/repo", "a" * 40, "b" * 40, parent=tmp_path)

    async def fail(*args):
        raise CheckoutFailure("Expected fetch failure")

    monkeypatch.setattr(workspace, "git", fail)

    async def run():
        with pytest.raises(CheckoutFailure):
            async with workspace:
                raise AssertionError("Must not reach candidate code")

    asyncio.run(run())
    assert list(tmp_path.iterdir()) == []


def test_real_git_command_and_size_enforcement(tmp_path):
    workspace = GitWorkspace("octocat/repo", "a" * 40, "b" * 40, max_bytes=1024)
    workspace.root = tmp_path

    async def run():
        version = await workspace.git("--version")
        assert version.startswith("git version")
        (tmp_path / "too-big").write_bytes(b"x" * 2048)
        with pytest.raises(CheckoutFailure, match="limits"):
            await workspace.git("--version")

    asyncio.run(run())


def test_scoped_checkout_marker_survives_until_cleanup(monkeypatch, tmp_path):
    import json
    from uuid import uuid4

    from codehound.core.execution_scope import ExecutionScope, execution_scope

    scope = ExecutionScope(str(uuid4()), str(uuid4()), str(uuid4()))
    workspace = GitWorkspace("octocat/repo", "a" * 40, "b" * 40, parent=tmp_path)
    observed = []

    async def fail(*args):
        marker = workspace.root / ".codehound-workspace.json"
        observed.append(json.loads(marker.read_text()))
        assert workspace.root.parent.name == scope.namespace
        raise CheckoutFailure("Expected fetch failure")

    monkeypatch.setattr(workspace, "git", fail)

    async def run():
        with execution_scope(scope), pytest.raises(CheckoutFailure):
            async with workspace:
                pass

    asyncio.run(run())
    assert observed[0]["claim_token"] == scope.claim_token
    assert list((tmp_path / scope.namespace).iterdir()) == []
