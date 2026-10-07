"""Retained AI patch corpus contracts; no reference outcomes are ground truth."""

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from codehound.execution.protocol import load_evidence

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,159}$")]
MAX_CORPUS_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_TOTAL_ARTIFACT_BYTES = 64 * 1024 * 1024


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @field_validator("schema_version", mode="before", check_fields=False)
    @classmethod
    def exact_schema_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be integer 1.")
        return value


class Source(Contract):
    id: Identifier
    url: str = Field(min_length=1, max_length=2000, pattern=r"^https?://")
    revision: str = Field(min_length=1, max_length=200)
    sha256: Digest


class Generation(Contract):
    kind: Literal["published_agent_prediction", "session_agent_prediction"]
    model: str = Field(min_length=1, max_length=200)
    agent: str = Field(min_length=1, max_length=200)
    source_id: Identifier


class Case(Contract):
    id: Identifier
    task_id: Identifier
    family: str = Field(min_length=1, max_length=200)
    split: Literal["development", "evaluation"]
    repository: str = Field(pattern=r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")
    base_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    issue_path: str = Field(min_length=1, max_length=500)
    issue_sha256: Digest
    patch_path: str = Field(min_length=1, max_length=500)
    patch_sha256: Digest
    generation: Generation


class Corpus(Contract):
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=160)
    purpose: Literal["development_pilot", "evaluation"] = "development_pilot"
    selection: str = Field(min_length=10, max_length=8000)
    sources: list[Source] = Field(min_length=1, max_length=100)
    cases: list[Case] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_inventory(self):
        source_ids = {source.id for source in self.sources}
        if len(source_ids) != len(self.sources):
            raise ValueError("Source IDs must be unique.")
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Case IDs must be unique.")
        splits: dict[tuple[str, str], str] = {}
        tasks: dict[str, tuple[str, str, str]] = {}
        identities: set[str] = set()
        for case in self.cases:
            if case.generation.source_id not in source_ids:
                raise ValueError("Generation source_id must identify a retained source.")
            for key in (("task", case.task_id), ("family", case.family)):
                if key in splits and splits[key] != case.split:
                    raise ValueError("Related tasks and families must stay in the same split.")
                splits[key] = case.split
            task_state = (case.repository, case.base_sha, case.issue_sha256)
            if case.task_id in tasks and tasks[case.task_id] != task_state:
                raise ValueError("Cases for the same task must share repository, base and issue.")
            tasks[case.task_id] = task_state
            identity = case_identity(case, self.sources)
            if identity in identities:
                raise ValueError("Duplicate patch identities cannot be counted as separate cases.")
            identities.add(identity)
            if self.purpose == "development_pilot" and case.split != "development":
                raise ValueError("A development pilot must use only the development split.")
        return self


def strict_json(raw: bytes, *, limit: int):
    try:
        return load_evidence(raw, limit=limit)
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds the parser limit.") from exc


def canonical_sha256(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def case_identity(case: Case, sources: list[Source] | tuple[Source, ...]) -> str:
    """Bind substantive artifacts and generation; display names/paths are relocatable."""
    return canonical_sha256(
        {
            "repository": case.repository,
            "base_sha": case.base_sha,
            "task_id": case.task_id,
            "issue_sha256": case.issue_sha256,
            "patch_sha256": case.patch_sha256,
            "generation": case.generation.model_dump(mode="json"),
            "sources": [
                source.model_dump(mode="json") for source in sorted(sources, key=lambda s: s.id)
            ],
        }
    )


def read_retained_file(root: Path, relative: str, *, max_bytes: int) -> bytes:
    """Read beneath root without following symlinks, including during path traversal."""
    parts = PurePosixPath(relative)
    if (
        not relative
        or parts.is_absolute()
        or ".." in parts.parts
        or "\\" in relative
        or "\x00" in relative
        or not parts.parts
    ):
        raise ValueError("Retained paths must stay within the corpus directory.")
    directory = None
    descriptor = None
    try:
        root = Path(root).absolute()
        directory = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in root.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        for part in parts.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(
            parts.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
            raise ValueError(f"Retained artifact must be a regular file <= {max_bytes} bytes.")
        chunks = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise ValueError("Retained artifact exceeds its size bound.")
        return data
    except OSError as exc:
        raise ValueError(
            f"Cannot read regular retained artifact {relative!r}: {exc.strerror}"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


@dataclass(frozen=True)
class PreparedCase:
    case: Case
    identity_sha256: str
    issue: bytes
    patch: bytes


def prepare_corpus(path: Path) -> tuple[Corpus, str, tuple[PreparedCase, ...]]:
    path = Path(path).absolute()
    raw = read_retained_file(path.parent, path.name, max_bytes=MAX_CORPUS_BYTES)
    corpus = Corpus.model_validate(strict_json(raw, limit=MAX_CORPUS_BYTES))
    prepared = []
    total_bytes = 0
    for case in corpus.cases:
        issue = read_retained_file(path.parent, case.issue_path, max_bytes=MAX_ARTIFACT_BYTES)
        patch = read_retained_file(path.parent, case.patch_path, max_bytes=MAX_ARTIFACT_BYTES)
        total_bytes += len(issue) + len(patch)
        if total_bytes > MAX_TOTAL_ARTIFACT_BYTES:
            raise ValueError("Corpus retained artifacts exceed the total size bound.")
        if not issue.strip() or not patch.strip():
            raise ValueError(f"Case {case.id}: issue and patch must be nonempty.")
        for name, data, expected in (
            ("issue", issue, case.issue_sha256),
            ("patch", patch, case.patch_sha256),
        ):
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError(f"Case {case.id}: retained {name} SHA256 mismatch.")
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"Case {case.id}: {name} must be UTF-8 text.") from exc
        prepared.append(PreparedCase(case, case_identity(case, corpus.sources), issue, patch))
    return corpus, canonical_sha256(corpus.model_dump(mode="json")), tuple(prepared)
