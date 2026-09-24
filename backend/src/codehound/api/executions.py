"""Queue only server-configured evaluations; candidate code never runs in HTTP handlers."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from codehound.api.verifications import principal, store, write_access
from codehound.db.jobs import JobStore
from codehound.db.store import StoreConflict
from codehound.evaluation.job_schemas import (
    ExecutionCreate,
    ExecutionDetail,
    ExecutionSummary,
    job_detail,
    job_summary,
)
from codehound.evaluation.registry import configured_image, load_profiles

router = APIRouter(tags=["execution"])


def jobs(request: Request):
    return JobStore(request.app.state.database)


def profiles():
    try:
        return load_profiles()
    except (ValueError, OSError):
        raise HTTPException(503, "Operator test profiles are unavailable.") from None


@router.get("/verifications/{identifier}/profiles")
def available_profiles(
    identifier: UUID, login=Depends(principal), records=Depends(store), queue=Depends(jobs)
):
    record = records.get(str(identifier), login["user"]["id"])
    if record is None:
        raise HTTPException(404, "Verification not found.")
    matched = [
        profile.public()
        for profile in profiles().values()
        if profile.repository.casefold() == record.repository.casefold()
    ]
    return {
        "profiles": matched,
        "configured": configured_image() is not None,
        "worker_online": queue.worker_online(),
    }


@router.post(
    "/verifications/{identifier}/executions",
    response_model=ExecutionSummary,
    status_code=202,
    dependencies=[Depends(write_access)],
)
def enqueue(
    identifier: UUID,
    submission: ExecutionCreate,
    response: Response,
    login=Depends(principal),
    records=Depends(store),
    queue=Depends(jobs),
    idempotency_key: Annotated[UUID | None, Header()] = None,
):
    if records.get(str(identifier), login["user"]["id"]) is None:
        raise HTTPException(404, "Verification not found.")
    image = configured_image()
    if image is None:
        raise HTTPException(503, "A trusted execution image has not been configured.")
    profile = profiles().get(submission.profile_id)
    if profile is None:
        raise HTTPException(422, "Select an available operator-owned test profile.")
    try:
        job, created = queue.enqueue(
            str(identifier),
            login["user"]["id"],
            profile,
            image,
            str(idempotency_key) if idempotency_key else None,
        )
    except StoreConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if job is None:
        raise HTTPException(404, "Verification not found.")
    response.status_code = 202 if created else 200
    response.headers["Location"] = f"/api/executions/{job.id}"
    return job_summary(job)


@router.get("/verifications/{identifier}/executions", response_model=list[ExecutionSummary])
def list_executions(
    identifier: UUID, login=Depends(principal), records=Depends(store), queue=Depends(jobs)
):
    if records.get(str(identifier), login["user"]["id"]) is None:
        raise HTTPException(404, "Verification not found.")
    return [job_summary(job) for job in queue.list(str(identifier), login["user"]["id"])]


@router.get("/executions/{identifier}", response_model=ExecutionDetail)
def get_execution(identifier: UUID, login=Depends(principal), queue=Depends(jobs)):
    job = queue.get(str(identifier), login["user"]["id"])
    if job is None:
        raise HTTPException(404, "Execution not found.")
    return job_detail(job)


@router.post(
    "/executions/{identifier}/cancel",
    response_model=ExecutionSummary,
    dependencies=[Depends(write_access)],
)
def cancel_execution(identifier: UUID, login=Depends(principal), queue=Depends(jobs)):
    job = queue.cancel(str(identifier), login["user"]["id"])
    if job is None:
        raise HTTPException(404, "Execution not found.")
    return job_summary(job)


@router.get("/executions/{identifier}/export")
def export_execution(identifier: UUID, login=Depends(principal), queue=Depends(jobs)):
    job = queue.get(str(identifier), login["user"]["id"])
    if job is None:
        raise HTTPException(404, "Execution not found.")
    return JSONResponse(
        job_detail(job).model_dump(mode="json"),
        headers={
            "Content-Disposition": f'attachment; filename="codehound-execution-{identifier}.json"',
            "X-Content-Type-Options": "nosniff",
        },
    )
