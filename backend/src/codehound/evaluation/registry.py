"""Server-owned evaluation profiles; HTTP callers can select IDs, never paths or code."""

import os
import re
from pathlib import Path

from pydantic import Field, model_validator

from codehound.execution.profiles import Contract, TrustedSuite
from codehound.repositories.urls import parse_pull_url


class EvaluationProfile(Contract):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    label: str = Field(min_length=1, max_length=120)
    repository: str = Field(max_length=201)
    coverage: str = Field(min_length=1, max_length=2000)
    visible: TrustedSuite
    hidden: TrustedSuite | None = None

    @model_validator(mode="after")
    def validate_repository(self):
        parse_pull_url(f"https://github.com/{self.repository}/pull/1")
        return self

    def public(self):
        return {
            "id": self.id,
            "label": self.label,
            "repository": self.repository,
            "coverage": self.coverage,
            "mode": "independent",
            "visible_cases": len(self.visible.cases),
            "hidden_cases": len(self.hidden.cases) if self.hidden else 0,
        }


def configured_image():
    image = os.getenv("CODEHOUND_EXECUTION_IMAGE_ID", "")
    return image if re.fullmatch(r"sha256:[0-9a-f]{64}", image) else None


def load_profiles():
    directory = Path(os.getenv("CODEHOUND_PROFILE_DIR", str(Path(__file__).parent / "profiles")))
    paths = sorted(directory.glob("*.json"))
    if len(paths) > 50:
        raise ValueError("At most 50 operator profiles are supported.")
    profiles = {}
    for path in paths:
        if path.is_symlink() or path.stat().st_size > 128 * 1024:
            raise ValueError("Invalid operator profile file.")
        profile = EvaluationProfile.model_validate_json(path.read_bytes())
        if profile.id in profiles:
            raise ValueError("Duplicate operator profile ID.")
        profiles[profile.id] = profile
    return profiles
