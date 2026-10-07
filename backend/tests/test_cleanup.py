import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from codehound.core.execution_scope import ExecutionScope, current_scope, execution_scope
from codehound.db.database import Database
from codehound.db.jobs import JobStore
from codehound.execution.cleanup import reconcile
from codehound.execution.docker import ContainerRunner, control


class Store:
    def __init__(self):
        self.identity = str(uuid4())
        self.live = set()

    def namespace(self):
        return self.identity

    def claim_is_live(self, identifier, token):
        return (identifier, token) in self.live

    def scope(self):
        return ExecutionScope(self.identity, str(uuid4()), str(uuid4()))


OLD = (datetime.now(UTC) - timedelta(hours=1)).isoformat()


def workspace(root, scope, *, created=OLD):
    path = root / scope.namespace / f"run-{scope.job_id}-{scope.claim_token}-random"
    path.mkdir(parents=True)
    (path / ".codehound-workspace.json").write_text(
        json.dumps(
            {
                "namespace": scope.namespace,
                "job_id": scope.job_id,
                "claim_token": scope.claim_token,
                "created_at": created,
            }
        )
    )
    return path


async def no_docker(*args):
    return 0, b"", b""


def test_namespace_is_persistent_and_database_specific(tmp_path):
    first = Database(f"sqlite:///{tmp_path / 'one.db'}")
    second = Database(f"sqlite:///{tmp_path / 'two.db'}")
    try:
        first.migrate()
        original = JobStore(first).namespace()
        first.migrate()
        assert JobStore(first).namespace() == original
        second.migrate()
        assert JobStore(second).namespace() != original
        assert not JobStore(first).claim_is_live(str(uuid4()), str(uuid4()))
    finally:
        first.close()
        second.close()


def test_execution_scope_is_task_local_and_resets(tmp_path):
    store = Store()
    runner = ContainerRunner("sha256:" + "a" * 64)

    async def task(scope):
        with execution_scope(scope):
            await asyncio.sleep(0)
            args = runner.container_args("test", tmp_path, [], ["python"])
            assert f"codehound.claim={scope.claim_token}" in args
            assert current_scope.get() == scope
        assert current_scope.get() is None

    async def run():
        await asyncio.gather(task(store.scope()), task(store.scope()))

    asyncio.run(run())
    assert not any(
        "codehound.claim=" in arg for arg in runner.container_args("x", tmp_path, [], [])
    )


def test_workspace_cleanup_only_expired_owned_resources(tmp_path):
    store = Store()
    expired, active, recent = store.scope(), store.scope(), store.scope()
    store.live.add((active.job_id, active.claim_token))
    stale = workspace(tmp_path, expired)
    retained = workspace(tmp_path, active)
    fresh = workspace(tmp_path, recent, created=datetime.now(UTC).isoformat())
    other = workspace(tmp_path, Store().scope())
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious").write_text("keep")
    (stale / "link").symlink_to(outside, target_is_directory=True)
    (stale.parent / "unrelated").mkdir()
    (stale.parent / "symlink").symlink_to(outside, target_is_directory=True)
    report = asyncio.run(reconcile(store, root=tmp_path, docker_control=no_docker))
    assert [item["name"] for item in report["workspaces"]] == [stale.name]
    assert stale.exists()
    report = asyncio.run(reconcile(store, root=tmp_path, apply=True, docker_control=no_docker))
    assert report["workspaces"][0]["action"] == "removed"
    assert not stale.exists()
    assert all(path.exists() for path in (retained, fresh, other, outside / "precious"))
    assert (stale.parent / "unrelated").exists()


def test_malformed_and_symlinked_workspace_markers_are_ignored(tmp_path):
    store = Store()
    malformed = workspace(tmp_path, store.scope())
    (malformed / ".codehound-workspace.json").write_text("{}")
    linked = workspace(tmp_path, store.scope())
    marker = linked / ".codehound-workspace.json"
    external = tmp_path / "external.json"
    marker.rename(external)
    marker.symlink_to(external)
    wrong_name = workspace(tmp_path, store.scope())
    wrong_name.rename(wrong_name.parent / "unrelated-name")
    report = asyncio.run(reconcile(store, root=tmp_path, apply=True, docker_control=no_docker))
    assert not report["workspaces"]
    assert malformed.exists() and linked.exists() and external.exists()


def test_container_cleanup_checks_namespace_age_live_claim_and_labels(tmp_path):
    store = Store()
    expired, active, recent, wrong = store.scope(), store.scope(), store.scope(), Store().scope()
    store.live.add((active.job_id, active.claim_token))
    entries = {}
    for index, scope in enumerate((expired, active, recent, wrong)):
        labels = scope.labels() | {"codehound.execution": "true"}
        entries[f"{index:012x}"] = (
            labels,
            datetime.now(UTC).isoformat() if scope == recent else OLD,
        )
    entries["eeeeeeeeeeee"] = ({"codehound.execution": "true"}, OLD)
    removed = []

    async def docker(*args):
        if args[0] == "ps":
            return 0, "\n".join(entries).encode(), b""
        if args[0] == "inspect":
            labels, created = entries[args[-1]]
            return 0, (json.dumps(labels) + "\n" + json.dumps(created)).encode(), b""
        assert args[:2] == ("rm", "--force")
        removed.append(args[-1])
        return 0, b"", b""

    report = asyncio.run(reconcile(store, root=tmp_path, docker_control=docker))
    assert report["containers"] == [{"id": "000000000000", "action": "eligible"}]
    assert removed == []
    report = asyncio.run(reconcile(store, root=tmp_path, apply=True, docker_control=docker))
    assert removed == ["000000000000"]
    assert not report["errors"]


def test_database_failure_never_authorizes_removal(tmp_path):
    store = Store()
    path = workspace(tmp_path, store.scope())

    def unavailable(*args):
        raise RuntimeError("database unavailable")

    store.claim_is_live = unavailable
    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(reconcile(store, root=tmp_path, apply=True, docker_control=no_docker))
    assert path.exists()


@pytest.mark.skipif(
    not os.getenv("CODEHOUND_TEST_IMAGE_ID"), reason="Trusted Docker image required"
)
def test_real_orphan_container_removed_live_claim_preserved(tmp_path):
    store = Store()
    expired, active = store.scope(), store.scope()
    store.live.add((active.job_id, active.claim_token))

    async def run():
        identifiers = []
        try:
            for scope in (expired, active):
                with execution_scope(scope):
                    args = ContainerRunner(os.environ["CODEHOUND_TEST_IMAGE_ID"]).container_args(
                        f"codehound-cleanup-test-{uuid4()}",
                        tmp_path,
                        [],
                        ["python", "-c", "import time; time.sleep(60)"],
                    )
                code, stdout, _ = await control(*args)
                assert code == 0
                identifiers.append(stdout.decode().strip())
                assert (await control("start", identifiers[-1]))[0] == 0
            report = await reconcile(store, root=tmp_path, apply=True, grace_seconds=0)
            assert not report["errors"]
            assert len(report["containers"]) == 1
            assert report["containers"][0]["action"] == "removed"
            assert (await control("inspect", identifiers[0]))[0] != 0
            assert (await control("inspect", identifiers[1]))[0] == 0
        finally:
            for identifier in identifiers:
                await control("rm", "--force", identifier)

    asyncio.run(run())
