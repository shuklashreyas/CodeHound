from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from codehound.evaluation.job_schemas import ExecutionStatus, ExecutionSummary
from codehound.repositories.urls import parse_pull_url


class VerificationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pr_url: str = Field(min_length=1, max_length=2048)
    issue_text: str = Field(min_length=10, max_length=20000)

    @field_validator("pr_url")
    @classmethod
    def validate_pr_url(cls, value):
        return parse_pull_url(value).url

    @field_validator("issue_text")
    @classmethod
    def validate_issue(cls, value):
        value = value.strip()
        if len(value) < 10 or "\x00" in value:
            raise ValueError("Describe the task in at least 10 characters without NUL bytes.")
        return value


class CheckResult(BaseModel):
    name: str
    status: Literal["not_run", "needs_review", "unknown", "pass", "fail", "inconclusive"]
    explanation: str


CHECKS = [
    "task_completion",
    "visible_tests",
    "hidden_tests",
    "regression_safety",
    "test_integrity",
    "requirement_adherence",
    "hardcoded_outputs",
    "functionality_preservation",
    "edge_case_coverage",
    "patch_scope",
    "implementation_complexity",
    "static_analysis",
    "downstream_impact",
]


def unrun_checks():
    return [
        CheckResult(name=name, status="not_run", explanation="No evaluator has run this check.")
        for name in CHECKS
    ]


class VerificationSummary(BaseModel):
    id: str
    repository: str
    pr_number: int
    pr_url: str
    issue_text: str
    title: str
    status: Literal["draft", "intaking", "ready", "failed"]
    created_at: datetime
    updated_at: datetime
    failure: dict | None


class VerificationReport(VerificationSummary):
    snapshot: dict | None
    attempts: list[dict]
    checks: list[CheckResult]
    confidence: None = None
    execution_status: ExecutionStatus | Literal["not_run"] = "not_run"
    latest_execution: ExecutionSummary | None = None


class VerificationList(BaseModel):
    items: list[VerificationSummary]
    has_more: bool
    next_offset: int | None
