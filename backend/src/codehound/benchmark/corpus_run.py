"""Offline, label-blind patch corpus evaluation in restricted candidate containers."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import signal
import stat
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from pydantic import Field, field_validator, model_validator

from codehound.benchmark.corpus import prepare_corpus
from codehound.benchmark.metrics import independent_decision, visible_decision
from codehound.benchmark.run import evidence_writer
from codehound.execution.independent import IndependentRunner
from codehound.execution.profiles import Contract, TrustedSuite
from codehound.execution.protocol import load_evidence
from codehound.execution.provenance import EvaluatorChanged, bind_source, controller_binding
from codehound.execution.repository_tests import RepositoryTestConfig, run_repository_tests
from codehound.execution.results import compare_tests, summarize_comparisons
from codehound.execution.static_analysis import analyze_static
from codehound.repositories.checkout import CheckoutFailure, Checkouts, GitWorkspace
from codehound.repositories.urls import parse_pull_url

_SOURCE_BINDING = bind_source(__file__)
MAX_PATCH_BYTES = 1024 * 1024
MAX_COPY_BYTES = 32 * 1024 * 1024
MAX_ENTRIES = 10000
EVALUATORS = ("visible_only", "independent", "repository_tests_only", "static_only")
CONTROLLERS = (
    "benchmark/corpus_run.py",
    "benchmark/corpus.py",
    "benchmark/metrics.py",
    "execution/independent.py",
    "execution/profiles.py",
    "execution/protocol.py",
    "execution/results.py",
    "execution/deadlines.py",
    "execution/docker.py",
    "execution/repository_tests.py",
    "execution/static_analysis.py",
    "execution/inspection/static_ruff.py",
)


class EvidenceInvalidated(RuntimeError):
    pass


class TaskExecution(Contract):
    task_id: str = Field(min_length=1, max_length=200)
    repository: str = Field(min_length=3, max_length=201)
    base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    issue_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    visible: TrustedSuite
    independent: TrustedSuite
    repository_tests: RepositoryTestConfig | None = None

    @model_validator(mode="after")
    def repository_is_public_github(self):
        parse_pull_url(f"https://github.com/{self.repository}/pull/1")
        return self


class ExecutionMapping(Contract):
    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_schema(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be integer 1.")
        return value

    schema_version: int = Field(default=1, ge=1, le=1)
    tasks: list[TaskExecution] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_tasks(self):
        if len({task.task_id for task in self.tasks}) != len(self.tasks):
            raise ValueError("Execution mapping task IDs must be unique.")
        return self


def regular_bytes(path, limit):
    """Bound trusted configuration reads and reject symlinks, devices and FIFOs."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Input must be a bounded regular file.")
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Input exceeds its byte limit.")
    return raw


def prepare_mapping(path):
    raw = regular_bytes(path, 2 * 1024 * 1024)
    mapping = ExecutionMapping.model_validate(load_evidence(raw, limit=2 * 1024 * 1024))
    canonical = json.dumps(mapping.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return mapping, hashlib.sha256(canonical.encode()).hexdigest()


def safe_patch_path(path):
    parts = PurePosixPath(path)
    return (
        0 < len(path) <= 512
        and re.fullmatch(r"[A-Za-z0-9_.+/-]+", path) is not None
        and not parts.is_absolute()
        and parts.as_posix() == path
        and all(part not in {".", "..", ".git", ".gitmodules"} for part in parts.parts)
    )


def patch_files(raw):
    """Conservatively support ordinary unified text diffs, with no special Git modes."""
    if not raw or len(raw) > MAX_PATCH_BYTES or b"\x00" in raw:
        raise ValueError("Patch is empty, binary or oversized.")
    text = raw.decode("utf-8")
    blocks = re.split(r"(?m)^(?=diff --git )", text)
    if blocks[0] or not 1 <= len(blocks) - 1 <= 100:
        raise ValueError("Patch requires bounded canonical Git diff headers.")
    changes = []
    for block in blocks[1:]:
        lines = block.splitlines()
        header = re.fullmatch(r"diff --git a/(\S+) b/(\S+)", lines[0])
        if not header or not all(safe_patch_path(path) for path in header.groups()):
            raise ValueError("Patch paths are unsafe or unsupported.")
        before, after = header.groups()
        if before != after:
            raise ValueError("Rename and copy patches are not supported.")
        metadata = []
        for line in lines[1:]:
            if line.startswith("@@ "):
                break
            metadata.append(line)
        if any(
            line.startswith(("old mode ", "new mode ", "rename ", "copy ", "GIT binary patch"))
            or "Binary files " in line
            or re.search(r"(?:^| )(?:120000|160000)(?:$| )", line)
            for line in metadata
        ):
            raise ValueError("Mode, binary, symlink and submodule mutations are unsupported.")
        mode_lines = [line for line in metadata if "file mode " in line]
        if any(
            line not in {"new file mode 100644", "deleted file mode 100644"} for line in mode_lines
        ):
            raise ValueError("Only ordinary regular text files may be added or deleted.")
        old_headers = [line[4:] for line in metadata if line.startswith("--- ")]
        new_headers = [line[4:] for line in metadata if line.startswith("+++ ")]
        if len(old_headers) != 1 or len(new_headers) != 1:
            raise ValueError("Patch requires exactly one old and new path per file.")
        if old_headers[0] not in {"a/" + before, "/dev/null"} or new_headers[0] not in {
            "b/" + after,
            "/dev/null",
        }:
            raise ValueError("Patch headers disagree.")
        changes.append(
            {
                "filename": after,
                "status": "added"
                if old_headers[0] == "/dev/null"
                else "removed"
                if new_headers[0] == "/dev/null"
                else "modified",
            }
        )
    if len({change["filename"] for change in changes}) != len(changes):
        raise ValueError("Patch repeats a file.")
    return changes


def copy_revision(source, destination):
    """Read repository files as bounded data only; never run setup or import Python."""
    if source.is_symlink() or not source.is_dir():
        raise ValueError("Repository source must be a regular directory.")
    destination.mkdir(mode=0o755)
    pending, entries, total = [source], 0, 0
    while pending:
        folder = pending.pop()
        for path in sorted(folder.iterdir()):
            if path.name == ".git":
                continue
            entries += 1
            if entries > MAX_ENTRIES:
                raise ValueError("Repository copy exceeds its entry limit.")
            mode = path.lstat().st_mode
            target = destination / path.relative_to(source)
            if stat.S_ISDIR(mode):
                target.mkdir(mode=0o755)
                pending.append(path)
            elif stat.S_ISREG(mode):
                content = regular_bytes(path, MAX_COPY_BYTES - total)
                total += len(content)
                target.write_bytes(content)
                target.chmod(0o644)
            else:
                raise ValueError("Repository copy contains a link or nonregular file.")


def workspace_digest(directory):
    """Fingerprint the complete bounded copied tree without following links."""
    digest = hashlib.sha256()
    pending, entries, total = [directory], 0, 0
    files = []
    while pending:
        folder = pending.pop()
        for path in sorted(folder.iterdir()):
            entries += 1
            if entries > MAX_ENTRIES:
                raise ValueError("Repository evidence exceeds its entry limit.")
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                pending.append(path)
            elif stat.S_ISREG(mode):
                content = regular_bytes(path, MAX_COPY_BYTES - total)
                total += len(content)
                files.append((path.relative_to(directory).as_posix(), content))
            else:
                raise ValueError("Repository evidence contains a link or nonregular file.")
    for name, content in sorted(files):
        encoded = name.encode()
        digest.update(len(encoded).to_bytes(8, "big") + encoded)
        digest.update(len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


async def apply_patch(workspace, raw):
    changes = patch_files(raw)
    environment = {
        "PATH": os.defpath,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CEILING_DIRECTORIES": str(workspace.parent),
        "LC_ALL": "C",
    }
    for options in (("--check",), ()):
        process = await asyncio.create_subprocess_exec(
            "git",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "credential.helper=",
            "apply",
            "--whitespace=nowarn",
            *options,
            "-",
            cwd=workspace,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        retained = bytearray()

        async def drain(stream):
            while chunk := await stream.read(8192):
                retained.extend(chunk[: max(0, 65536 - len(retained))])

        readers = [
            asyncio.create_task(drain(process.stdout)),
            asyncio.create_task(drain(process.stderr)),
        ]
        finished = asyncio.create_task(process.wait())
        try:
            async with asyncio.timeout(15):
                process.stdin.write(raw)
                await process.stdin.drain()
                process.stdin.close()
                await finished
                await asyncio.gather(*readers)
            if process.returncode:
                raise ValueError("Patch does not apply to the pinned baseline.")
        finally:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await asyncio.wait_for(asyncio.gather(process.wait(), *readers), timeout=5)
    workspace_digest(workspace)  # Validate the resulting tree before any container executes it.
    return changes


def repository_decision(result):
    if not result or result.get("status") != "completed" or not result.get("test_comparison"):
        return "abstain"
    candidate = result.get("candidate")
    if not candidate:
        return "abstain"
    from codehound.execution.repository_tests import RepositoryExecutionResult

    return visible_decision(RepositoryExecutionResult(**candidate))


def static_decision(result):
    if (
        not result
        or result.get("status") != "completed"
        or not result.get("coverage", {}).get("comparison_complete")
    ):
        return "abstain"
    return "reject" if result["counts"]["new"] else "accept"


async def run_corpus(
    corpus_path,
    mapping_path,
    image_id,
    *,
    max_seconds=600,
    runner=None,
    workspace_factory=GitWorkspace,
    static_analyzer=analyze_static,
    repository_analyzer=run_repository_tests,
    checkpoint=None,
    progress=None,
):
    if type(max_seconds) is not int or not 1 <= max_seconds <= 7200:
        raise ValueError("Corpus deadline must be between 1 and 7200 seconds.")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("An immutable trusted local image ID is required.")
    controller = controller_binding(CONTROLLERS)
    corpus, corpus_sha, prepared = prepare_corpus(corpus_path)
    mapping, mapping_sha = prepare_mapping(mapping_path)
    configurations = {task.task_id: task for task in mapping.tasks}
    runner = runner or IndependentRunner(image_id)
    rows = [
        {
            "case_id": item.case.id,
            "task_id": item.case.task_id,
            "split": item.case.split,
            "group": item.case.family,
            "repository": item.case.repository,
            "base_commit": item.case.base_sha,
            "generation": item.case.generation.model_dump(mode="json"),
            "case_identity_sha256": item.identity_sha256,
            "patch_sha256": hashlib.sha256(item.patch).hexdigest(),
            "issue_sha256": hashlib.sha256(item.issue).hexdigest(),
            "status": "not_run",
            "decisions": {name: "abstain" for name in EVALUATORS},
            "suites": {},
            "assessment": None,
            "repository_tests": None,
            "static_analysis": None,
        }
        for item in prepared
    ]
    record = {
        "schema_version": 1,
        "kind": "label_blind_patch_corpus_execution",
        "corpus_name": corpus.name,
        "corpus_purpose": corpus.purpose,
        "source_provenance": [source.model_dump(mode="json") for source in corpus.sources],
        "status": "running",
        "corpus_sha256": corpus_sha,
        "mapping_sha256": mapping_sha,
        "image_id": image_id,
        "started_at": datetime.now(UTC).isoformat(),
        "rows": rows,
        "controller_binding": controller.to_dict(),
        "limitations": [
            "Operator JSON contracts check selected behavior only; "
            "they do not establish task correctness.",
            "visible_only means operator probes authored for this experiment; "
            "it does not establish which tests were visible to the original agent.",
            "No review labels, model judgments or source-reported resolution labels "
            "affect execution.",
            "Missing execution mappings remain explicit unsupported rows and abstentions.",
            "Repository tests share a process with candidate code; "
            "static findings are review evidence.",
            "Only bounded ordinary text patches and regular repository trees are supported.",
        ],
    }
    current = None
    started = time.monotonic()

    def save():
        record["execution_counts"] = {
            status: sum(row["status"] == status for row in rows)
            for status in sorted({row["status"] for row in rows})
        }
        if checkpoint:
            checkpoint(record)

    save()
    timer = asyncio.timeout(max_seconds)
    try:
        async with timer:
            for item, row in zip(prepared, rows, strict=True):
                current = row
                config = configurations.get(item.case.task_id)
                if config is None:
                    row.update(status="unsupported", reason="missing_operator_execution_mapping")
                    save()
                    continue
                if (config.repository.casefold(), config.base_commit, config.issue_sha256) != (
                    item.case.repository.casefold(),
                    item.case.base_sha,
                    row["issue_sha256"],
                ):
                    raise ValueError("Execution mapping does not bind the corpus task identity.")
                row.update(
                    status="running",
                    repository=config.repository,
                    base_commit=config.base_commit,
                    profile_sha256={
                        "visible": config.visible.sha256,
                        "hidden": config.independent.sha256,
                    },
                    repository_configuration_sha256=config.repository_tests.sha256
                    if config.repository_tests
                    else None,
                )
                save()
                try:
                    patch_files(item.patch)
                    async with workspace_factory(
                        config.repository, config.base_commit, config.base_commit
                    ) as retained:
                        with TemporaryDirectory(prefix="codehound-corpus-") as temporary:
                            root = Path(temporary)
                            copy_revision(retained.baseline, root / "baseline")
                            copy_revision(retained.baseline, root / "candidate")
                            changes = await apply_patch(root / "candidate", item.patch)
                            checkouts = Checkouts(
                                root / "baseline",
                                root / "candidate",
                                config.base_commit,
                                config.base_commit,
                            )
                            row["baseline_workspace_sha256"] = workspace_digest(checkouts.baseline)
                            row["candidate_workspace_sha256"] = workspace_digest(
                                checkouts.candidate
                            )
                            for name, suite in (
                                ("visible", config.visible),
                                ("hidden", config.independent),
                            ):
                                if progress:
                                    progress(f"{item.case.id}: {name}")
                                baseline = await runner.run(checkouts.baseline, suite)
                                row["suites"][name] = {
                                    "baseline": baseline.to_dict(),
                                    "test_suite_sha256": suite.sha256,
                                }
                                save()
                                candidate = await runner.run(checkouts.candidate, suite)
                                row["suites"][name].update(
                                    candidate=candidate.to_dict(),
                                    test_comparison=compare_tests(baseline, candidate),
                                )
                                if name == "visible":
                                    row["decisions"]["visible_only"] = visible_decision(candidate)
                                save()
                            row["assessment"] = summarize_comparisons(row["suites"])
                            row["decisions"]["independent"] = independent_decision(
                                row["assessment"]
                            )
                            save()
                            if config.repository_tests:
                                row["repository_tests"] = await repository_analyzer(
                                    checkouts, config.repository_tests, image_id
                                )
                                row["decisions"]["repository_tests_only"] = repository_decision(
                                    row["repository_tests"]
                                )
                                save()
                            row["static_analysis"] = await static_analyzer(
                                {"files": changes}, checkouts, image_id
                            )
                            row["decisions"]["static_only"] = static_decision(
                                row["static_analysis"]
                            )
                            if (
                                workspace_digest(checkouts.baseline),
                                workspace_digest(checkouts.candidate),
                            ) != (
                                row["baseline_workspace_sha256"],
                                row["candidate_workspace_sha256"],
                            ):
                                raise EvidenceInvalidated("Pinned execution workspaces changed.")
                    row["status"] = "evaluated"
                except TimeoutError:
                    if timer.expired():
                        raise
                    row.update(status="not_run", reason="execution_setup_or_evaluator_timeout")
                    row["decisions"] = {name: "abstain" for name in EVALUATORS}
                    row["assessment"] = None
                except (ValueError, OSError, CheckoutFailure):
                    row.update(status="not_run", reason="unsupported_patch_or_execution_setup")
                    row["decisions"] = {name: "abstain" for name in EVALUATORS}
                    row["assessment"] = None
                save()
                current = None
        record["status"] = "completed"
    except TimeoutError:
        if not timer.expired():
            raise
        record["status"] = "timeout"
        if current and current["status"] == "running":
            current["status"] = "interrupted"
    except asyncio.CancelledError:
        record["status"] = "cancelled"
        if current and current["status"] == "running":
            current["status"] = "interrupted"
        save()
        raise
    except (ValueError, OSError, EvaluatorChanged, EvidenceInvalidated):
        record["status"] = "invalidated"
        for row in rows:
            row.update(
                status="invalidated",
                decisions={name: "abstain" for name in EVALUATORS},
                assessment=None,
            )
    try:
        controller.ensure_current()
        _, final_corpus_sha, final_cases = prepare_corpus(corpus_path)
        _, final_mapping_sha = prepare_mapping(mapping_path)
        if (
            final_corpus_sha,
            final_mapping_sha,
            [item.identity_sha256 for item in final_cases],
        ) != (corpus_sha, mapping_sha, [item.identity_sha256 for item in prepared]):
            raise ValueError("Corpus or operator mapping changed.")
    except (ValueError, OSError, EvaluatorChanged):
        record["status"] = "invalidated"
        for row in rows:
            row.update(
                status="invalidated",
                decisions={name: "abstain" for name in EVALUATORS},
                assessment=None,
            )
    record["finished_at"] = datetime.now(UTC).isoformat()
    record["duration_seconds"] = round(time.monotonic() - started, 3)
    save()
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-seconds", type=int, default=600)
    args = parser.parse_args()
    result = asyncio.run(
        run_corpus(
            args.corpus,
            args.mapping,
            args.image_id,
            max_seconds=args.max_seconds,
            checkpoint=evidence_writer(args.output),
            progress=lambda value: print(value, flush=True),
        )
    )
    print(f"Corpus {result['status']}; evidence: {args.output}")
    if result["status"] != "completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
