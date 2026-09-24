"""Reconcile expired job resources. CLI defaults to a read-only inventory."""

import argparse
import asyncio
import json
import re
import shutil
from datetime import UTC, datetime

from dotenv import load_dotenv
from starlette.concurrency import run_in_threadpool

from codehound.core.execution_scope import ExecutionScope, workspace_root
from codehound.db.database import Database
from codehound.db.jobs import JobStore
from codehound.execution.docker import control


def old_enough(value, grace_seconds):
    try:
        created = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (
            created.tzinfo is not None
            and (datetime.now(UTC) - created).total_seconds() >= grace_seconds
        )
    except (ValueError, TypeError, AttributeError):
        return False


async def reconcile(store, *, apply=False, grace_seconds=120, root=None, docker_control=control):
    if type(grace_seconds) is not int or not 0 <= grace_seconds <= 86400:
        raise ValueError("Grace period must be between 0 and 86400 seconds.")
    namespace = await run_in_threadpool(store.namespace)
    report = {
        "namespace": namespace,
        "dry_run": not apply,
        "containers": [],
        "workspaces": [],
        "errors": [],
    }

    async def eligible(scope):
        return scope.namespace == namespace and not await run_in_threadpool(
            store.claim_is_live, scope.job_id, scope.claim_token
        )

    try:
        code, stdout, _ = await docker_control(
            "ps", "-a", "--filter", f"label=codehound.namespace={namespace}", "--format", "{{.ID}}"
        )
        if code:
            raise RuntimeError("Container inventory unavailable.")
        for identifier in stdout.decode("ascii").splitlines()[:200]:
            if not re.fullmatch(r"[0-9a-f]{12,64}", identifier):
                continue
            code, payload, _ = await docker_control(
                "inspect", "--format", "{{json .Config.Labels}}\n{{json .Created}}", identifier
            )
            if code:
                continue  # A normal runner may already have removed it.
            try:
                labels_line, created_line = payload.decode().splitlines()
                labels, created = json.loads(labels_line), json.loads(created_line)
                if labels.get("codehound.execution") != "true":
                    continue
                scope = ExecutionScope(
                    labels["codehound.namespace"],
                    labels["codehound.job"],
                    labels["codehound.claim"],
                )
                if not old_enough(created, grace_seconds) or not await eligible(scope):
                    continue
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
            action = "eligible"
            if apply:
                if not await eligible(scope):
                    continue
                code, _, _ = await docker_control("rm", "--force", identifier)
                action = "removed" if code == 0 else "removal_failed"
            report["containers"].append({"id": identifier, "action": action})
    except (OSError, RuntimeError, TimeoutError, UnicodeError):
        report["errors"].append("Container cleanup could not complete.")

    parent = (root or workspace_root()) / namespace
    if parent.is_symlink() or not parent.is_dir():
        return report
    try:
        # Namespace parents are controller-owned and never mounted in a candidate container.
        for directory in sorted(parent.iterdir())[:200]:
            if directory.is_symlink() or not directory.is_dir():
                continue
            marker = directory / ".codehound-workspace.json"
            try:
                if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 2048:
                    continue
                metadata = json.loads(marker.read_text())
                scope = ExecutionScope(
                    metadata["namespace"], metadata["job_id"], metadata["claim_token"]
                )
                if not directory.name.startswith(f"run-{scope.job_id}-{scope.claim_token}-"):
                    continue
                if not old_enough(metadata["created_at"], grace_seconds) or not await eligible(
                    scope
                ):
                    continue
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                continue
            action = "eligible"
            if apply:
                if not await eligible(scope):
                    continue
                await asyncio.to_thread(shutil.rmtree, directory)
                action = "removed"
            report["workspaces"].append({"name": directory.name, "action": action})
    except OSError:
        report["errors"].append("Workspace cleanup could not complete.")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--grace-seconds", type=int, default=120)
    args = parser.parse_args()
    if args.env_file:
        load_dotenv(args.env_file)
    database = Database()
    database.migrate()
    try:
        report = asyncio.run(
            reconcile(JobStore(database), apply=args.apply, grace_seconds=args.grace_seconds)
        )
        print(json.dumps(report, indent=2))
        return (
            2
            if report["errors"]
            or any(item["action"] == "removal_failed" for item in report["containers"])
            else 0
        )
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())
