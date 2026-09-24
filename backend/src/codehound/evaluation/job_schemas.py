from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from codehound.core.time import utc
from codehound.evaluation.registry import EvaluationProfile

ExecutionStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


class ExecutionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")


class ExecutionSummary(BaseModel):
    id: str
    verification_id: str
    profile_id: str
    profile: dict
    status: ExecutionStatus
    stage: str
    cancel_requested: bool
    assessment: dict | None
    failure: dict | None
    base_sha: str
    head_sha: str
    diff_sha256: str
    image_id: str
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class ExecutionDetail(ExecutionSummary):
    artifact: dict | None


def job_summary(job):
    return ExecutionSummary(
        id=job.id,
        verification_id=job.verification_id,
        profile_id=job.profile_id,
        profile=EvaluationProfile.model_validate(job.profile_snapshot).public(),
        status=job.status,
        stage=job.stage,
        cancel_requested=job.cancel_requested,
        assessment=job.assessment,
        failure=job.failure,
        base_sha=job.base_sha,
        head_sha=job.head_sha,
        diff_sha256=job.diff_sha256,
        image_id=job.image_id,
        created_at=utc(job.created_at),
        updated_at=utc(job.updated_at),
        finished_at=utc(job.finished_at) if job.finished_at else None,
    )


def job_detail(job):
    return ExecutionDetail(**job_summary(job).model_dump(), artifact=job.artifact)


def execution_checks(checks, job):
    if job is None or job.status != "completed" or not job.artifact:
        return
    by_name = {check.name: check for check in checks}
    suites = job.artifact.get("suites", {})
    for name, suite in suites.items():
        check = by_name.get(f"{name}_tests")
        if check is None:
            continue
        candidate = suite["candidate"]
        cases = (candidate.get("test_report") or {}).get("tests", [])
        passed = sum(case["outcome"] == "passed" for case in cases)
        failed = sum(case["outcome"] == "failed" for case in cases)
        other = len(cases) - passed - failed
        if candidate["status"] != "completed" or not cases:
            check.status = "inconclusive"
        elif failed:
            check.status = "fail"
        elif not other:
            check.status = "pass"
        else:
            check.status = "inconclusive"
        check.explanation = (
            f"Configured {name} suite: {passed} passed, {failed} failed, {other} unverified. "
            "Other behavior has not been tested."
        )
    regressions = sum(len(suite["test_comparison"]["regressions"]) for suite in suites.values())
    check = by_name["regression_safety"]
    check.status = "fail" if regressions else "unknown"
    check.explanation = (
        f"{regressions} passing baseline checks fail on the candidate."
        if regressions
        else "No regressions observed in configured checks. Untested behavior remains unverified."
    )

    integrity = job.artifact.get("test_integrity")
    if integrity and integrity["status"] != "not_applicable":
        check = by_name["test_integrity"]
        count = len(integrity["findings"])
        uncertain = len(integrity["unverified"])
        if count:
            check.status = "needs_review"
        elif integrity["status"] == "inconclusive":
            check.status = "inconclusive"
        check.explanation = (
            f"Structural inspection of {integrity['files_examined']} changed Python test files: "
            f"{count} review findings, {uncertain} unverified inspections. "
            "These are source-level hints, not proof of assertion strength or intent."
        )

    requirements = job.artifact.get("requirement_evidence", {}).get("requirements", [])
    if requirements:
        contradicted = sum(item["candidate"]["status"] == "contradicted" for item in requirements)
        supported = sum(
            item["candidate"]["status"] == "supported_by_checks" for item in requirements
        )
        check = by_name["requirement_adherence"]
        check.status = "fail" if contradicted else "unknown"
        check.explanation = (
            f"Operator-defined requirements: {supported} supported by mapped examples, "
            f"{contradicted} contradicted, "
            f"{len(requirements) - supported - contradicted} unverified. "
            "Coverage of the submitted issue has not been established."
        )

    impact = job.artifact.get("python_impact")
    if impact:
        count = len(impact["new_syntax_errors"])
        check = by_name["static_analysis"]
        check.status = (
            "fail" if count else "inconclusive" if impact["status"] == "inconclusive" else "unknown"
        )
        check.explanation = (
            f"{count} new Python syntax errors observed. "
            "Only syntax and static imports were inspected; "
            "lint, types, and security remain unverified."
        )
        affected = {
            item["path"]
            for revision in impact["revisions"].values()
            for item in revision["affected"]
        }
        check = by_name["downstream_impact"]
        check.status = "needs_review" if affected else "unknown"
        check.explanation = (
            f"{len(affected)} displayed downstream Python files may depend on changed paths. "
            "Import reachability is a review hint, not proof of a regression."
        )
