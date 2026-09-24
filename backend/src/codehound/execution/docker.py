"""Run Python tests in a resource-limited, offline Docker container.

Callers provide server-owned workspaces and independently retained tests. This
module never clones repositories or accepts host mount paths from HTTP requests.
Exit codes are execution evidence, not proof against malicious in-process test
interference. Candidate code is never executed directly on the host.
"""

import asyncio
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from uuid import uuid4

from codehound.execution.results import PREFIX, decode_report


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    image_id: str
    output_truncated: bool
    oom_killed: bool
    timeout_seconds: int
    network: str = "none"
    memory_mb: int = 512
    cpu_limit: float = 1.0
    test_report: dict | None = None
    evidence_error: str | None = None
    evaluator_sha256: str | None = None
    evidence_source: str = "in_process_pytest"
    call_response: dict | None = None
    case_evidence: list[dict] | None = None

    def to_dict(self):
        return asdict(self)


def mount_path(path: Path):
    path = path.resolve(strict=True)
    if not path.is_dir() or path == Path(path.anchor):
        raise ValueError("A server-owned workspace directory is required.")
    if any(char in str(path) for char in (",", "\n", "\r")):
        raise ValueError("Docker mount paths may not contain commas or line breaks.")
    return str(path)


async def control(*args, timeout=15):
    """Small, bounded Docker control commands (never candidate commands)."""
    process = await asyncio.create_subprocess_exec(
        "docker",
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    return process.returncode, stdout[:8192], stderr[:8192]


class ContainerRunner:
    def __init__(self, image_id: str, *, timeout_seconds=30, output_limit=1_000_000):
        # A locally built, immutable image ID; never pull caller-selected images at runtime.
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ValueError("Configure a trusted local Docker image ID (sha256:...).")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
            raise ValueError("Execution timeout must be between 1 and 300 seconds.")
        if type(output_limit) is not int or not 1024 <= output_limit <= 1_000_000:
            raise ValueError("Output limit must be between 1024 and 1000000 bytes.")
        self.image_id = image_id
        self.timeout = timeout_seconds
        self.output_limit = output_limit

    def container_args(self, name, workspace, mounts, command, *, interactive=False):
        args = [
            "create",
            "--name",
            name,
            "--label",
            "codehound.execution=true",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=64",
            "--cpus=1",
            "--memory=512m",
            "--memory-swap=512m",
            "--user=65534:65534",
            "--init",
            "--log-driver=none",
            "--stop-timeout=1",
            "--workdir=/workspace",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=67108864,mode=1777",
            "--mount",
            f"type=bind,src={mount_path(workspace)},dst=/workspace,readonly",
        ]
        for source, destination in mounts:
            args.extend(
                ["--mount", f"type=bind,src={mount_path(source)},dst={destination},readonly"]
            )
        if interactive:
            args.append("--interactive")
        args.extend(
            [
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--env",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
                self.image_id,
                "/usr/bin/timeout",
                "--signal=KILL",
                str(self.timeout),
                *command,
            ]
        )
        return args

    async def run_container(self, workspace, mounts, command, *, input_data=None):
        name = f"codehound-{uuid4().hex}"
        args = self.container_args(
            name, workspace, mounts, command, interactive=input_data is not None
        )
        start = time.monotonic()
        process = None
        stdout = bytearray()
        stderr = bytearray()
        output_truncated = False
        forced_status = None
        exit_code = None
        oom_killed = False
        try:
            code, _, message = await control(*args)
            if code:
                return ExecutionResult(
                    "unavailable",
                    None,
                    "",
                    message.decode(errors="replace"),
                    round(time.monotonic() - start, 3),
                    self.image_id,
                    False,
                    False,
                    self.timeout,
                )
            process = await asyncio.create_subprocess_exec(
                "docker",
                "start",
                "--attach",
                *(["--interactive"] if input_data is not None else []),
                name,
                stdin=asyncio.subprocess.PIPE
                if input_data is not None
                else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            if input_data is not None:
                try:
                    process.stdin.write(input_data)
                    await asyncio.wait_for(process.stdin.drain(), timeout=5)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    process.stdin.close()
            limit_reached = asyncio.Event()

            async def read(stream, target):
                nonlocal output_truncated
                while chunk := await stream.read(8192):
                    remaining = max(0, self.output_limit - len(stdout) - len(stderr))
                    target.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        output_truncated = True
                        limit_reached.set()

            readers = [
                asyncio.create_task(read(process.stdout, stdout)),
                asyncio.create_task(read(process.stderr, stderr)),
            ]
            finished = asyncio.create_task(process.wait())
            limit = asyncio.create_task(limit_reached.wait())
            try:
                done, _ = await asyncio.wait(
                    [finished, limit],
                    timeout=self.timeout + 10,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if limit in done:
                    forced_status = "output_limit"
                elif not done:
                    forced_status = "timeout"
                if forced_status:
                    await control("kill", name, timeout=5)
                await asyncio.wait_for(finished, timeout=5)
                await asyncio.wait_for(asyncio.gather(*readers), timeout=5)
            finally:
                limit.cancel()
                for task in [finished, *readers]:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(limit, finished, *readers, return_exceptions=True)
            code, raw, _ = await control("inspect", "--format", "{{json .State}}", name)
            if code:
                forced_status = "infrastructure_error"
            else:
                state = json.loads(raw)
                exit_code = state["ExitCode"]
                oom_killed = state.get("OOMKilled", False)
                if state.get("Error"):
                    forced_status = "infrastructure_error"
            status = forced_status or (
                "oom" if oom_killed else "timeout" if exit_code in (124, 137) else "completed"
            )
            if output_truncated:
                status = "output_limit"
            return ExecutionResult(
                status,
                exit_code,
                stdout.decode(errors="replace"),
                stderr.decode(errors="replace"),
                round(time.monotonic() - start, 3),
                self.image_id,
                output_truncated,
                oom_killed,
                self.timeout,
            )
        except (FileNotFoundError, OSError, TimeoutError, ValueError, KeyError):
            return ExecutionResult(
                "infrastructure_error",
                None,
                stdout.decode(errors="replace"),
                "Docker execution could not complete.",
                round(time.monotonic() - start, 3),
                self.image_id,
                output_truncated,
                oom_killed,
                self.timeout,
            )
        finally:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
            try:
                await control("rm", "--force", name, timeout=10)
            except (OSError, TimeoutError):
                pass


class DockerRunner(ContainerRunner):
    """Compatibility pytest runner; assertions share a process with candidate code."""

    def create_args(self, name, workspace, tests, token="test"):
        return self.container_args(
            name,
            workspace,
            [(tests, "/tests"), (Path(__file__).parent, "/harness")],
            ["python", "-I", "/harness/pytest_runner.py", token],
        )

    async def run(self, workspace: Path, tests: Path):
        token = uuid4().hex
        harness = Path(__file__).parent
        before = hashlib.sha256(
            (harness / "pytest_runner.py").read_bytes() + (harness / "pytest.ini").read_bytes()
        ).hexdigest()
        result = await self.run_container(
            workspace,
            [(tests, "/tests"), (harness, "/harness")],
            ["python", "-I", "/harness/pytest_runner.py", token],
        )
        report, error = decode_report(result.stdout, token, result.exit_code)
        output = result.stdout
        if report is not None:
            output = "\n".join(
                line for line in output.splitlines() if not line.startswith(PREFIX + token + ":")
            )
        after = hashlib.sha256(
            (harness / "pytest_runner.py").read_bytes() + (harness / "pytest.ini").read_bytes()
        ).hexdigest()
        if before != after:
            report, error = None, "evaluator_changed"
        return replace(
            result, stdout=output, test_report=report, evidence_error=error, evaluator_sha256=before
        )
