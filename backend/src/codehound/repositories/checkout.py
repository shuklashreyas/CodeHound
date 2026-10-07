"""Disposable Git checkouts of pinned public revisions, without executing repository code."""

import asyncio
import json
import os
import re
import signal
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from codehound.core.execution_scope import current_scope, workspace_root
from codehound.repositories.urls import parse_pull_url


class CheckoutFailure(Exception):
    pass


@dataclass(frozen=True)
class Checkouts:
    baseline: Path
    candidate: Path
    base_sha: str
    head_sha: str


def directory_usage(root: Path):
    """Measure regular files without following repository-controlled symlinks."""
    total = count = 0
    pending = [root]
    while pending:
        folder = pending.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        count += 1
                        total += entry.stat(follow_symlinks=False).st_size
        except FileNotFoundError:
            continue  # Git can replace pack/index files while a fetch is running.
    return total, count


class GitWorkspace:
    """No credentials, user Git config, hooks, filters, submodules, or repository scripts.

    Size checks are sampled, not filesystem quotas. Run this service on a dedicated
    worker volume with a quota before exposing bulk repository intake in production.
    """

    def __init__(
        self,
        repository: str,
        base_sha: str,
        head_sha: str,
        *,
        timeout_seconds=90,
        max_bytes=256 * 1024 * 1024,
        max_files=100000,
        parent: Path | None = None,
    ):
        parse_pull_url(f"https://github.com/{repository}/pull/1")
        if not all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (base_sha, head_sha)):
            raise ValueError("Checkout requires full immutable commit SHAs.")
        if (
            not 1 <= timeout_seconds <= 300
            or not 1024 <= max_bytes <= 1024**3
            or not 1 <= max_files <= 200000
        ):
            raise ValueError("Invalid checkout resource limit.")
        self.repository = repository
        self.base_sha = base_sha
        self.head_sha = head_sha
        self.timeout = timeout_seconds
        self.max_bytes = max_bytes
        self.max_files = max_files
        self.parent = parent
        self.temporary = None
        self.root = None

    def environment(self):
        # Do not inherit OAuth secrets, Git credential helpers, SSH agents, or user config.
        return {
            "PATH": os.defpath + os.pathsep + "/opt/homebrew/bin" + os.pathsep + "/usr/local/bin",
            "HOME": str(self.root),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "LC_ALL": "C",
        }

    def git_args(self, *args):
        return [
            "git",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "credential.helper=",
            "-c",
            "protocol.file.allow=never",
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "http.followRedirects=false",
            "-c",
            "submodule.recurse=false",
            *args,
        ]

    async def git(self, *args):
        process = await asyncio.create_subprocess_exec(
            *self.git_args(*args),
            cwd=self.root,
            env=self.environment(),
            start_new_session=True,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        output = bytearray()
        overflow = asyncio.Event()

        async def consume(stream):
            while chunk := await stream.read(8192):
                remaining = max(0, 128 * 1024 - len(output))
                output.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    overflow.set()

        finished = asyncio.create_task(process.wait())
        readers = [
            asyncio.create_task(consume(process.stdout)),
            asyncio.create_task(consume(process.stderr)),
        ]
        try:
            async with asyncio.timeout(self.timeout):
                while process.returncode is None:
                    size, count = await asyncio.to_thread(directory_usage, self.root)
                    if size > self.max_bytes or count > self.max_files or overflow.is_set():
                        raise CheckoutFailure(
                            "Repository exceeded checkout size, file, or output limits."
                        )
                    try:
                        await asyncio.wait_for(asyncio.shield(finished), timeout=0.2)
                    except TimeoutError:
                        continue
                await asyncio.gather(*readers)
            size, count = await asyncio.to_thread(directory_usage, self.root)
            if size > self.max_bytes or count > self.max_files or overflow.is_set():
                raise CheckoutFailure("Repository exceeded checkout size, file, or output limits.")
            if process.returncode:
                raise CheckoutFailure(
                    "Git could not fetch or check out the pinned public revisions."
                )
            return output.decode(errors="replace").strip()
        except TimeoutError as exc:
            raise CheckoutFailure("Repository checkout exceeded its time limit.") from exc
        finally:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
            for reader in readers:
                if not reader.done():
                    reader.cancel()
            await asyncio.gather(finished, *readers, return_exceptions=True)

    async def __aenter__(self):
        scope = current_scope.get()
        parent = self.parent
        prefix = "codehound-checkout-"
        if scope:
            parent = (parent or workspace_root()) / scope.namespace
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if parent.is_symlink():
                raise CheckoutFailure("Managed workspace parent must not be a symlink.")
            prefix = f"run-{scope.job_id}-{scope.claim_token}-"
        self.temporary = tempfile.TemporaryDirectory(prefix=prefix, dir=parent)
        self.root = Path(self.temporary.name)
        try:
            if scope:
                marker = {
                    "namespace": scope.namespace,
                    "job_id": scope.job_id,
                    "claim_token": scope.claim_token,
                    "created_at": datetime.now(UTC).isoformat(),
                }
                (self.root / ".codehound-workspace.json").write_text(json.dumps(marker))
            async with asyncio.timeout(self.timeout):
                await self.git("init", "--bare", "objects.git")
                await self.git(
                    "--git-dir=objects.git",
                    "fetch",
                    "--no-tags",
                    "--no-recurse-submodules",
                    "--depth=1",
                    f"https://github.com/{self.repository}.git",
                    self.base_sha,
                    self.head_sha,
                )
                for label, sha in (("baseline", self.base_sha), ("candidate", self.head_sha)):
                    await self.git(
                        "--git-dir=objects.git", "worktree", "add", "--detach", label, sha
                    )
                    actual = await self.git("-C", label, "rev-parse", "HEAD")
                    if actual != sha:
                        raise CheckoutFailure("Git checked out an unexpected revision.")
            return Checkouts(
                self.root / "baseline", self.root / "candidate", self.base_sha, self.head_sha
            )
        except BaseException:
            await asyncio.to_thread(self.temporary.cleanup)
            raise

    async def __aexit__(self, *_):
        await asyncio.to_thread(self.temporary.cleanup)
