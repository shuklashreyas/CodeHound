"""Operator-owned JSON test profiles. Never load these from a candidate checkout."""

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

SYMBOL = r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ValueExpectation(Contract):
    kind: Literal["value"]
    value: JsonValue


class ExceptionExpectation(Contract):
    kind: Literal["exception"]
    exception: str = Field(pattern=SYMBOL, max_length=200)


class TestCase(Contract):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$")
    args: list[JsonValue] = Field(default_factory=list, max_length=100)
    kwargs: dict[str, JsonValue] = Field(default_factory=dict, max_length=100)
    expect: Annotated[ValueExpectation | ExceptionExpectation, Field(discriminator="kind")]


class TrustedSuite(Contract):
    schema_version: Literal[1] = 1
    name: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")
    module: str = Field(pattern=SYMBOL, max_length=200)
    function: str = Field(pattern=SYMBOL, max_length=200)
    source_directory: str = Field(default="src", pattern=r"^[A-Za-z0-9_./-]{1,200}$")
    result_encoding: Literal["json", "dataclass"] = "json"
    timeout_seconds: int = Field(default=5, ge=1, le=10)
    cases: list[TestCase] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def check_inventory(self):
        if self.source_directory.startswith("/") or ".." in self.source_directory.split("/"):
            raise ValueError("Source directory must stay inside the workspace.")
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Test case IDs must be unique.")
        if len(self.canonical_bytes()) > 32768:
            raise ValueError("Test profile exceeds 32 KiB.")
        return self

    def canonical_bytes(self):
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, allow_nan=False, separators=(",", ":")
        ).encode()

    @property
    def sha256(self):
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def load(cls, path: Path):
        if path.stat().st_size > 32768:
            raise ValueError("Test profile exceeds 32 KiB.")
        return cls.model_validate_json(path.read_bytes())
