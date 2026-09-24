"""Authenticated, owner-scoped submissions and read-only GitHub evidence intake."""

import asyncio
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from codehound.api import github
from codehound.db.store import StoreConflict, VerificationStore, utc
from codehound.evaluation.schemas import (
    VerificationCreate,
    VerificationList,
    VerificationReport,
    VerificationSummary,
    unrun_checks,
)
from codehound.repositories.github_client import GitHubClient, GitHubFailure
from codehound.repositories.intake import collect_snapshot
from codehound.repositories.urls import parse_pull_url

router = APIRouter(prefix="/verifications", tags=["verifications"])
logger = logging.getLogger(__name__)


def principal(request: Request):
    login = github.session(request)
    identifier = login["user"].get("id")
    if type(identifier) is not int or identifier <= 0:
        raise HTTPException(401, "Sign in again to enable saved verifications.")
    return login


def store(request: Request):
    return VerificationStore(request.app.state.database)


def write_access(request: Request):
    if request.headers.get("X-CodeHound-Request") != "1":
        raise HTTPException(403, "A same-origin CodeHound request is required.")
    if request.headers.get("origin") not in (None, github.settings()["origin"]):
        raise HTTPException(403, "Invalid request origin.")


def summary(item):
    return VerificationSummary(
        id=item.id,
        repository=item.repository,
        pr_number=item.pr_number,
        pr_url=item.pr_url,
        issue_text=item.issue_text,
        title=item.title,
        status=item.status,
        created_at=utc(item.created_at),
        updated_at=utc(item.updated_at),
        failure=item.failure,
    )


def report(item):
    checks = unrun_checks()
    if item.snapshot:
        test_changes = [
            f
            for f in item.snapshot["observations"]
            if f["kind"]
            in (
                "test_file_changed",
                "configuration_changed",
            )
        ]
        integrity = next(c for c in checks if c.name == "test_integrity")
        integrity.status = "needs_review" if test_changes else "unknown"
        integrity.explanation = (
            "Test or configuration paths changed. Human or structural review is needed."
            if test_changes
            else "No test-path changes were identified; assertion integrity is unverified."
        )
    return VerificationReport(
        **summary(item).model_dump(), snapshot=item.snapshot, attempts=item.attempts, checks=checks
    )


@router.post(
    "", response_model=VerificationSummary, status_code=201, dependencies=[Depends(write_access)]
)
def create_verification(
    submission: VerificationCreate,
    response: Response,
    login=Depends(principal),
    database=Depends(store),
    idempotency_key: Annotated[UUID | None, Header()] = None,
):
    try:
        item, created = database.create(
            login["user"]["id"],
            login["user"]["login"],
            submission,
            str(idempotency_key) if idempotency_key else None,
        )
    except StoreConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    response.status_code = 201 if created else 200
    response.headers["Location"] = f"/api/verifications/{item.id}"
    return summary(item)


@router.get("", response_model=VerificationList)
def list_verifications(
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    login=Depends(principal),
    database=Depends(store),
):
    items = database.list(login["user"]["id"], limit, offset)
    more = len(items) > limit
    return VerificationList(
        items=[summary(item) for item in items[:limit]],
        has_more=more,
        next_offset=offset + limit if more else None,
    )


@router.get("/{identifier}", response_model=VerificationReport)
def get_verification(identifier: UUID, login=Depends(principal), database=Depends(store)):
    item = database.get(str(identifier), login["user"]["id"])
    if not item:
        raise HTTPException(404, "Verification not found.")
    return report(item)


@router.post(
    "/{identifier}/intake", response_model=VerificationReport, dependencies=[Depends(write_access)]
)
async def intake_verification(
    identifier: UUID,
    request: Request,
    login=Depends(principal),
    database=Depends(store),
):
    try:
        item, claim = await run_in_threadpool(database.claim, str(identifier), login["user"]["id"])
    except StoreConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if item is None:
        raise HTTPException(404, "Verification not found.")
    if claim is None:
        return report(item)
    snapshot = None
    failure = None
    try:
        async with asyncio.timeout(90):
            async with GitHubClient(login["token"]) as provider:
                snapshot = await collect_snapshot(parse_pull_url(item.pr_url), provider)
    except GitHubFailure as exc:
        failure = {"code": exc.code, "message": exc.message}
        if exc.status == 401:
            github.sessions.pop(request.cookies.get(github.SESSION_COOKIE, ""), None)
    except TimeoutError:
        failure = {"code": "intake_timeout", "message": "Intake exceeded its 90-second limit."}
    except Exception:
        # Do not serialize upstream exception messages, which can contain request credentials.
        logger.error("Unexpected intake failure for verification %s", identifier)
        failure = {"code": "intake_error", "message": "Intake failed unexpectedly. Retry later."}
    try:
        item = await run_in_threadpool(
            database.finish, item.id, claim, snapshot=snapshot, failure=failure
        )
    except StoreConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    return report(item)


@router.get("/{identifier}/export")
def export_verification(identifier: UUID, login=Depends(principal), database=Depends(store)):
    item = database.get(str(identifier), login["user"]["id"])
    if not item:
        raise HTTPException(404, "Verification not found.")
    return JSONResponse(
        report(item).model_dump(mode="json"),
        headers={
            "Content-Disposition": f'attachment; filename="codehound-{identifier}.json"',
            "X-Content-Type-Options": "nosniff",
        },
    )
