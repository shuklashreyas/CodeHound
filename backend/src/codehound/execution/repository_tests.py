"""Optional frozen repository pytest evidence; never an independent evaluator.

Only operator profiles select paths. Repository Python is copied as data on the
host and executed only in the restricted container, including baseline conftest.
"""

import hashlib
import json
import re
import stat
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from uuid import uuid4

from pydantic import Field, model_validator

from codehound.execution.docker import ContainerRunner, ExecutionResult
from codehound.execution.profiles import Contract
from codehound.execution.provenance import bind_source, controller_binding, guard_evaluator
from codehound.execution.results import PREFIX, compare_tests, decode_report

_SOURCE_BINDING = bind_source(__file__)
CONTROLLERS = (
    "execution/repository_tests.py",
    "execution/profiles.py",
    "execution/docker.py",
    "execution/results.py",
    "execution/protocol.py",
)
SOURCE = "repository_controlled_in_process_pytest"
IMPORT_PREFIX = "CODEHOUND_REPOSITORY_IMPORTS_V1:"
MAX_FILES = 10000
MAX_BYTES = 32 * 1024 * 1024
LIMITATIONS = [
    "Repository tests and baseline conftest share a process with repository code; "
    "evidence can be manipulated.",
    "This lower-trust evidence never changes the independent assessment or requirement coverage.",
    "Only operator-selected baseline tests, data, and ancestor conftest/__init__ files are frozen; "
    "candidate-added tests are excluded.",
    "Repository pytest configuration and automatic plugins are ignored; "
    "dependencies must exist in the trusted image.",
    "Hidden files and cache directories are excluded; "
    "external fixtures and full repository coverage are not guaranteed.",
    "Only configured target packages receive import-origin checks; "
    "these checks are also repository-controlled in-process observations.",
]


def validate_relative(value, *, allow_root=False):
    if allow_root and value == ".":
        return value
    path = PurePosixPath(value)
    if (
        not value
        or value == "."
        or len(value) > 200
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in (".", "..") or part.startswith((".", "-")) for part in path.parts)
        or any(not (char.isascii() and (char.isalnum() or char in "_/.-")) for char in value)
    ):
        raise ValueError("Repository paths must be safe relative paths without hidden components.")
    return value


class RepositoryTestConfig(Contract):
    test_paths: list[str] = Field(min_length=1, max_length=20)
    source_directories: list[str] = Field(default_factory=lambda: ["src", "."], max_length=10)
    target_packages: list[str] = Field(default_factory=list, max_length=20)
    timeout_seconds: int = Field(default=60, ge=1, le=120)

    @model_validator(mode="after")
    def validate_paths(self):
        for value in self.test_paths:
            validate_relative(value)
        for value in self.source_directories:
            validate_relative(value, allow_root=True)
        paths = [PurePosixPath(value) for value in self.test_paths]
        if len(set(paths)) != len(paths) or any(
            first in second.parents for first in paths for second in paths if first != second
        ):
            raise ValueError("Repository test selections must be unique and non-overlapping.")
        if len(set(self.source_directories)) != len(self.source_directories):
            raise ValueError("Repository source directories must be unique.")
        if len(set(self.target_packages)) != len(self.target_packages) or any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", name) for name in self.target_packages
        ):
            raise ValueError("Target packages must be unique top-level Python module names.")
        return self

    @property
    def sha256(self):
        raw = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class FrozenRepositoryTests:
    directory: Path
    config: RepositoryTestConfig
    provenance: dict


def _safe_input(root, relative):
    current = root
    for part in relative.parts:
        current /= part
        mode = current.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValueError("Repository test inputs may not contain symlinks.")
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise ValueError("Repository test inputs must be regular files or directories.")
    return current


@contextmanager
def freeze_repository_tests(baseline: Path, config: RepositoryTestConfig, baseline_sha: str):
    """Freeze baseline inputs once, without importing any repository Python."""
    if baseline.is_symlink():
        raise ValueError("Baseline workspace may not be a symlink.")
    root = baseline.resolve(strict=True)
    files = set()
    visited = 0
    for selection in config.test_paths:
        relative = PurePosixPath(selection)
        selected = _safe_input(root, relative)
        if selected.is_file() and selected.suffix != ".py":
            raise ValueError("Select Python test files or test directories.")
        pending = [selected]
        while pending:
            path = pending.pop()
            visited += 1
            if visited > 20000:
                raise ValueError("Repository test inputs exceed the entry limit.")
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError("Repository test inputs may not contain symlinks.")
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError("Repository test inputs must contain regular files only.")
            if path.name.startswith(".") or path.name == "__pycache__":
                continue
            if stat.S_ISDIR(mode):
                for child in path.iterdir():
                    pending.append(child)
                    if visited + len(pending) > 20000:
                        raise ValueError("Repository test inputs exceed the entry limit.")
            else:
                files.add(path.relative_to(root))
                if len(files) > MAX_FILES:
                    raise ValueError("Repository tests exceed the file limit.")
        # Preserve fixture and package context from the baseline only.
        parent = relative.parent
        while True:
            for name in ("conftest.py", "__init__.py"):
                source = root / parent / name
                if source.exists() or source.is_symlink():
                    _safe_input(root, parent / name)
                    if not source.is_file():
                        raise ValueError("Ancestor pytest inputs must be regular files.")
                    files.add(Path(parent / name))
            if parent == PurePosixPath("."):
                break
            parent = parent.parent
    if not files:
        raise ValueError("Selected repository test inputs are empty.")
    if len(files) > MAX_FILES:
        raise ValueError("Repository tests exceed the file limit.")
    with TemporaryDirectory(prefix="codehound-repository-tests-") as temporary:
        frozen = Path(temporary)
        frozen.chmod(0o755)
        manifest, total = [], 0
        digest = hashlib.sha256()
        for relative in sorted(files):
            source = _safe_input(root, relative)
            remaining = MAX_BYTES - total
            if source.stat().st_size > remaining or len(relative.as_posix()) > 512:
                raise ValueError("Repository tests exceed the size or path limit.")
            with source.open("rb") as handle:
                content = handle.read(remaining + 1)
            total += len(content)
            if total > MAX_BYTES:
                raise ValueError("Repository tests exceed the size limit.")
            destination = frozen / relative
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            destination.write_bytes(content)
            destination.chmod(0o444)
            name = relative.as_posix().encode()
            digest.update(len(name).to_bytes(8, "big"))
            digest.update(name)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
            manifest.append(
                {
                    "path": relative.as_posix(),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "bytes": len(content),
                }
            )
        yield FrozenRepositoryTests(
            frozen,
            config,
            {
                "baseline_sha": baseline_sha,
                "test_suite_sha256": digest.hexdigest(),
                "configuration_sha256": config.sha256,
                "test_paths": config.test_paths,
                "source_directories": config.source_directories,
                "target_packages": config.target_packages,
                "files": manifest,
                "file_count": len(manifest),
                "total_bytes": total,
            },
        )


@dataclass(frozen=True)
class RepositoryExecutionResult(ExecutionResult):
    target_imports: list[dict] | None = None


def decode_imports(stdout, token, targets):
    marker = IMPORT_PREFIX + token + ":"
    frames = [line[len(marker) :] for line in stdout.splitlines() if line.startswith(marker)]
    if len(frames) != 1 or len(frames[0]) > 200000:
        return None, "missing_import_provenance"
    try:
        entries = json.loads(frames[0])
        if not isinstance(entries, list) or len(entries) > 1000:
            raise ValueError("Invalid import inventory")
        seen = set()
        for item in entries:
            if not isinstance(item, dict) or set(item) != {"module", "paths"}:
                raise ValueError("Invalid import entry")
            name, paths = item["module"], item["paths"]
            if (
                not isinstance(name, str)
                or len(name) > 300
                or name.split(".")[0] not in targets
                or name in seen
                or not isinstance(paths, list)
                or not 1 <= len(paths) <= 20
                or any(
                    not isinstance(path, str)
                    or len(path) > 2048
                    or not path.startswith("/workspace/")
                    or ".." in PurePosixPath(path).parts
                    for path in paths
                )
            ):
                raise ValueError("Target import is not from the pinned workspace")
            seen.add(name)
        if not set(targets) <= seen:
            raise ValueError("Target package was not imported")
    except (ValueError, TypeError, KeyError, RecursionError):
        return None, "target_import_provenance_invalid"
    return entries, None


class RepositoryTestRunner(ContainerRunner):
    """Run frozen baseline pytest inputs against a mounted revision."""

    async def run(self, workspace: Path, frozen: FrozenRepositoryTests):
        controller = controller_binding(CONTROLLERS)
        harness = Path(__file__).parent
        inputs = ("repository_pytest_runner.py", "pytest_runner.py", "pytest.ini")

        def fingerprint():
            return hashlib.sha256(
                controller.to_dict()["sha256"].encode()
                + b"".join((harness / name).read_bytes() for name in inputs)
            ).hexdigest()

        before, token = fingerprint(), uuid4().hex
        config = json.dumps(frozen.config.model_dump(), separators=(",", ":"))
        result = await self.run_container(
            workspace,
            [(frozen.directory, "/repository-tests"), (harness, "/harness")],
            ["python", "-I", "/harness/repository_pytest_runner.py", token, config],
        )
        report, error = decode_report(result.stdout, token, result.exit_code)
        imports, import_error = decode_imports(result.stdout, token, frozen.config.target_packages)
        if import_error and frozen.config.target_packages:
            report, error = None, import_error
        output = result.stdout
        if report is not None:
            output = "\n".join(
                line
                for line in output.splitlines()
                if not line.startswith((PREFIX + token + ":", IMPORT_PREFIX + token + ":"))
            )
        if before != fingerprint():
            report, error = None, "evaluator_changed"
        values = asdict(result)
        values.update(
            stdout=output,
            test_report=report,
            evidence_error=error,
            evaluator_sha256=before,
            evidence_source=SOURCE,
            target_imports=imports,
            controller_binding=controller.to_dict(),
        )
        controller.ensure_current()
        return RepositoryExecutionResult(**values)


@guard_evaluator(*CONTROLLERS)
async def run_repository_tests(checkouts, config, image_id, *, runner=None, on_progress=None):
    artifact = {
        "mode": "frozen_baseline",
        "trust": "repository_controlled",
        "evidence_source": SOURCE,
        "affects_assessment": False,
        "status": "unavailable",
        "provenance": None,
        "baseline": None,
        "candidate": None,
        "comparison": "inconclusive",
        "test_comparison": None,
        "limitations": list(LIMITATIONS),
    }
    runner = runner or RepositoryTestRunner(image_id, timeout_seconds=config.timeout_seconds)
    try:
        with freeze_repository_tests(checkouts.baseline, config, checkouts.base_sha) as frozen:
            artifact["provenance"] = frozen.provenance
            if on_progress:
                await on_progress("repository_tests_baseline")
            baseline = await runner.run(checkouts.baseline, frozen)
            if on_progress:
                await on_progress("repository_tests_candidate")
            candidate = await runner.run(checkouts.candidate, frozen)
            comparison = compare_tests(baseline, candidate)
            artifact.update(
                status="completed",
                baseline=baseline.to_dict(),
                candidate=candidate.to_dict(),
                comparison=comparison["verdict"],
                test_comparison=comparison,
            )
    except (ValueError, OSError):
        artifact["error_code"] = "repository_test_inputs_unavailable"
    return artifact
