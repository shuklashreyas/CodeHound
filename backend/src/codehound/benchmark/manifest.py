"""Trusted operator manifests for offline verification experiments."""

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from codehound.execution.profiles import Contract, TrustedSuite
from codehound.execution.verify import suite_digest


class Candidate(Contract):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")
    workspace: str = Field(min_length=1, max_length=500)
    label: Literal["valid", "invalid", "unreviewed"]
    label_reason: str = Field(min_length=10, max_length=2000)


class Task(Contract):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")
    family: str = Field(min_length=1, max_length=100)
    split: Literal["demo", "train", "evaluation"]
    issue: str = Field(min_length=10, max_length=20000)
    baseline: str = Field(min_length=1, max_length=500)
    visible_profile: str = Field(min_length=1, max_length=500)
    independent_profile: str = Field(min_length=1, max_length=500)
    candidates: list[Candidate] = Field(min_length=1, max_length=20)


class Manifest(Contract):
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=120)
    dataset_kind: Literal["synthetic_demo", "human_reviewed"]
    provenance: str = Field(min_length=10, max_length=4000)
    tasks: list[Task] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_inventory(self):
        if len({task.id for task in self.tasks}) != len(self.tasks):
            raise ValueError("Task IDs must be unique.")
        families = {}
        for task in self.tasks:
            if len({case.id for case in task.candidates}) != len(task.candidates):
                raise ValueError("Candidate IDs must be unique within each task.")
            if task.family in families and families[task.family] != task.split:
                raise ValueError("Related task families must stay in the same split.")
            families[task.family] = task.split
            if self.dataset_kind == "synthetic_demo" and task.split != "demo":
                raise ValueError("Synthetic demonstrations must use the demo split.")
        return self


def retained_path(root: Path, relative: str):
    parts = PurePosixPath(relative)
    if not relative or parts.is_absolute() or ".." in parts.parts or "\x00" in relative:
        raise ValueError("Manifest paths must stay within the dataset directory.")
    path = root / relative
    if any(
        root.joinpath(*parts.parts[:index]).is_symlink() for index in range(1, len(parts.parts) + 1)
    ):
        raise ValueError("Dataset paths may not be symlinks.")
    if not path.resolve(strict=True).is_relative_to(root.resolve()):
        raise ValueError("Dataset path escaped its directory.")
    return path


def prepare_manifest(path: Path):
    if path.is_symlink() or path.stat().st_size > 256 * 1024:
        raise ValueError("Manifest must be a regular JSON file no larger than 256 KiB.")
    raw = path.read_bytes()
    manifest = Manifest.model_validate_json(raw)
    root = path.resolve().parent
    prepared = []
    for task in manifest.tasks:
        baseline = retained_path(root, task.baseline)
        profiles = {
            "visible": TrustedSuite.load(retained_path(root, task.visible_profile)),
            "hidden": TrustedSuite.load(retained_path(root, task.independent_profile)),
        }
        candidates = []
        for candidate in task.candidates:
            workspace = retained_path(root, candidate.workspace)
            candidates.append((candidate, workspace, suite_digest(workspace)))
        prepared.append((task, baseline, suite_digest(baseline), profiles, candidates))
    canonical = json.dumps(
        manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()
    return manifest, hashlib.sha256(canonical).hexdigest(), prepared
