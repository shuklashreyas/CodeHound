"""Short database transactions; no network requests hold a database connection."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer

from codehound.db.models import Verification
from codehound.evaluation.schemas import VerificationCreate
from codehound.repositories.urls import parse_pull_url


class StoreConflict(Exception):
    pass


class VerificationStore:
    def __init__(self, database):
        self.engine = database.engine

    def get(self, identifier: str, owner_id: int):
        with Session(self.engine) as db:
            return db.scalar(
                select(Verification).where(
                    Verification.id == identifier,
                    Verification.owner_id == owner_id,
                )
            )

    def list(self, owner_id: int, limit: int, offset: int):
        with Session(self.engine) as db:
            return list(
                db.scalars(
                    select(Verification)
                    .options(defer(Verification.snapshot), defer(Verification.attempts))
                    .where(Verification.owner_id == owner_id)
                    .order_by(Verification.created_at.desc(), Verification.id.desc())
                    .offset(offset)
                    .limit(limit + 1)
                )
            )

    def create(self, owner_id: int, owner_login: str, submission: VerificationCreate, key=None):
        reference = parse_pull_url(submission.pr_url)
        now = datetime.now(UTC)
        item = Verification(
            id=str(uuid4()),
            owner_id=owner_id,
            owner_login=owner_login,
            idempotency_key=key,
            repository=reference.repository,
            pr_number=reference.number,
            pr_url=reference.url,
            issue_text=submission.issue_text,
            title=f"Pull request #{reference.number}",
            status="draft",
            created_at=now,
            updated_at=now,
            attempts=[],
        )
        with Session(self.engine, expire_on_commit=False) as db:
            try:
                db.add(item)
                db.commit()
                return item, True
            except IntegrityError:
                db.rollback()
                if key is None:
                    raise
                existing = db.scalar(
                    select(Verification).where(
                        Verification.owner_id == owner_id,
                        Verification.idempotency_key == key,
                    )
                )
                if existing is None:
                    raise
                if (
                    existing.pr_url != submission.pr_url
                    or existing.issue_text != submission.issue_text
                ):
                    raise StoreConflict(
                        "This idempotency key was used for different submission inputs."
                    ) from None
                return existing, False

    def claim(self, identifier: str, owner_id: int):
        now = datetime.now(UTC)
        claim = str(uuid4())
        with Session(self.engine, expire_on_commit=False) as db:
            item = db.scalar(
                select(Verification).where(
                    Verification.id == identifier,
                    Verification.owner_id == owner_id,
                )
            )
            if item is None or item.status == "ready":
                return item, None
            if len(item.attempts) >= 20:
                raise StoreConflict("Intake retry limit reached. Create a new submission.")
            eligible = or_(
                Verification.status.in_(["draft", "failed"]),
                and_(
                    Verification.status == "intaking",
                    Verification.lease_until < now,
                ),
            )
            attempts = [dict(attempt) for attempt in item.attempts]
            if item.status == "intaking" and attempts:
                attempts[-1].update(
                    status="interrupted",
                    finished_at=now.isoformat(),
                    failure={
                        "code": "lease_expired",
                        "message": "The previous intake did not finish.",
                    },
                )
            attempts.append(
                {"id": claim, "started_at": now.isoformat(), "status": "intaking", "failure": None}
            )
            result = db.execute(
                update(Verification)
                .where(
                    Verification.id == identifier,
                    Verification.owner_id == owner_id,
                    Verification.updated_at == item.updated_at,
                    eligible,
                )
                .values(
                    status="intaking",
                    claim_token=claim,
                    lease_until=now + timedelta(seconds=120),
                    failure=None,
                    attempts=attempts,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                raise StoreConflict("Intake is already running for this submission.")
            db.commit()
            db.refresh(item)
            return item, claim

    def finish(self, identifier: str, claim: str, *, snapshot=None, failure=None):
        if bool(snapshot) == bool(failure):
            raise ValueError("Completion requires either captured evidence or a failure.")
        with Session(self.engine, expire_on_commit=False) as db:
            item = db.scalar(
                select(Verification).where(
                    Verification.id == identifier,
                    Verification.claim_token == claim,
                    Verification.status == "intaking",
                )
            )
            if item is None:
                raise StoreConflict("A newer intake attempt superseded this one.")
            now = datetime.now(UTC)
            attempts = [dict(attempt) for attempt in item.attempts]
            attempts[-1].update(
                finished_at=now.isoformat(),
                status="failed" if failure else "ready",
                failure=failure,
            )
            values = dict(
                status="failed" if failure else "ready",
                snapshot=snapshot,
                failure=failure,
                attempts=attempts,
                claim_token=None,
                lease_until=None,
                updated_at=now,
            )
            if snapshot:
                values["title"] = snapshot["pull_request"]["title"]
            result = db.execute(
                update(Verification)
                .where(
                    Verification.id == identifier,
                    Verification.claim_token == claim,
                    Verification.status == "intaking",
                )
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                db.rollback()
                raise StoreConflict("A newer intake attempt superseded this one.")
            db.commit()
            db.refresh(item)
            return item
