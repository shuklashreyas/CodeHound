"""Server-owned evaluation profiles; HTTP callers can select IDs, never paths or code."""

import os
import re
import stat
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from codehound.execution.profiles import Contract, TrustedSuite
from codehound.execution.repository_tests import RepositoryTestConfig
from codehound.repositories.urls import parse_pull_url


class CaseReference(Contract):
    suite: Literal["visible", "hidden"]
    case_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$")


class Requirement(Contract):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,79}$")
    description: str = Field(min_length=1, max_length=1000)
    cases: list[CaseReference] = Field(default_factory=list, max_length=100)


class EvaluationProfile(Contract):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    label: str = Field(min_length=1, max_length=120)
    repository: str = Field(max_length=201)
    coverage: str = Field(min_length=1, max_length=2000)
    visible: TrustedSuite
    hidden: TrustedSuite | None = None
    repository_tests: RepositoryTestConfig | None = None
    requirements: list[Requirement] = Field(default_factory=list, max_length=100)
    execution_image_id: str | None = Field(default=None, max_length=71)

    @field_validator("execution_image_id")
    @classmethod
    def validate_image_id(cls, value):
        if value is not None and not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ValueError("Execution images must be immutable lowercase SHA-256 IDs.")
        return value

    @model_validator(mode="after")
    def validate_repository(self):
        parse_pull_url(f"https://github.com/{self.repository}/pull/1")
        if len({item.id for item in self.requirements}) != len(self.requirements):
            raise ValueError("Requirement IDs must be unique.")
        inventory = {
            (name, case.id)
            for name, suite in (("visible", self.visible), ("hidden", self.hidden))
            if suite is not None
            for case in suite.cases
        }
        for requirement in self.requirements:
            references = [(case.suite, case.case_id) for case in requirement.cases]
            if len(set(references)) != len(references) or not set(references) <= inventory:
                raise ValueError("Requirement references must identify unique configured cases.")
        return self

    def public(self):
        return {
            "id": self.id,
            "label": self.label,
            "repository": self.repository,
            "coverage": self.coverage,
            "mode": "independent",
            "requirements": [
                {"id": item.id, "description": item.description, "mapped_cases": len(item.cases)}
                for item in self.requirements
            ],
            "visible_cases": len(self.visible.cases),
            "hidden_cases": len(self.hidden.cases) if self.hidden else 0,
            "repository_tests": (
                {
                    "mode": "frozen_baseline",
                    "selected_paths": len(self.repository_tests.test_paths),
                    "target_packages": self.repository_tests.target_packages,
                    "timeout_seconds": self.repository_tests.timeout_seconds,
                    "trust": "repository_controlled",
                }
                if self.repository_tests
                else None
            ),
        }


def configured_image(profile: EvaluationProfile | None = None):
    if profile is not None and profile.execution_image_id is not None:
        return profile.execution_image_id
    image = os.getenv("CODEHOUND_EXECUTION_IMAGE_ID", "")
    return image if re.fullmatch(r"sha256:[0-9a-f]{64}", image) else None


def read_profile(path):
    """Open the retained file once; never follow links or read special files."""
    limit = 128 * 1024
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
            raise ValueError("Invalid operator profile file.")
        raw = bytearray()
        while len(raw) <= limit:
            chunk = os.read(descriptor, min(65536, limit + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) > limit:
            raise ValueError("Operator profile exceeds 128 KiB.")
        return bytes(raw)
    finally:
        os.close(descriptor)


def load_profiles():
    directory = Path(os.getenv("CODEHOUND_PROFILE_DIR", str(Path(__file__).parent / "profiles")))
    paths = []
    for path in directory.glob("*.json"):
        paths.append(path)
        if len(paths) > 50:
            raise ValueError("At most 50 operator profiles are supported.")
    paths.sort()
    profiles = {}
    for path in paths:
        profile = EvaluationProfile.model_validate_json(read_profile(path))
        if profile.id in profiles:
            raise ValueError("Duplicate operator profile ID.")
        profiles[profile.id] = profile
    return profiles
