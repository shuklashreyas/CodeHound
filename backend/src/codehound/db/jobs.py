"""Durable execution queue with owner scoping, claims, cancellation, and crash recovery."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import JSON, case, delete, func, literal, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer

from codehound.db.models import ExecutionJob, ExecutionNamespace, Verification, WorkerHeartbeat
from codehound.db.store import StoreConflict


class QueueCapacity(Exception):
    """A bounded queue has no capacity for another execution."""


class QueueConfiguration(Exception):
    pass


def queue_limits():
    values = []
    for key, default, maximum in (
        ("CODEHOUND_MAX_PENDING_PER_ACCOUNT", "5", 100),
        ("CODEHOUND_MAX_PENDING_EXECUTIONS", "50", 1000),
    ):
        try:
            value = int(os.getenv(key, default))
            if not 1 <= value <= maximum:
                raise ValueError
        except ValueError:
            raise QueueConfiguration("Execution queue capacity is misconfigured.") from None
        values.append(value)
    return values


class JobStore:
    def __init__(self, database):
        self.engine = database.engine

    def get(self, identifier, owner_id):
        with Session(self.engine) as db:
            return db.scalar(
                select(ExecutionJob).where(
                    ExecutionJob.id == identifier, ExecutionJob.owner_id == owner_id
                )
            )

    def list(self, verification_id, owner_id, limit=20):
        with Session(self.engine) as db:
            return list(
                db.scalars(
                    select(ExecutionJob)
                    .options(defer(ExecutionJob.artifact))
                    .where(
                        ExecutionJob.verification_id == verification_id,
                        ExecutionJob.owner_id == owner_id,
                    )
                    .order_by(ExecutionJob.created_at.desc(), ExecutionJob.id.desc())
                    .limit(limit)
                )
            )

    def latest(self, verification_id, owner_id):
        with Session(self.engine) as db:
            return db.scalar(
                select(ExecutionJob)
                .where(
                    ExecutionJob.verification_id == verification_id,
                    ExecutionJob.owner_id == owner_id,
                )
                .order_by(ExecutionJob.created_at.desc(), ExecutionJob.id.desc())
                .limit(1)
            )

    def enqueue(self, verification_id, owner_id, profile, image_id, key=None):
        account_limit, total_limit = queue_limits()
        self.reap()
        now = datetime.now(UTC)
        with Session(self.engine, expire_on_commit=False) as db:
            # Serialize admission so simultaneous requests cannot overfill the queue.
            # This lock covers only the short database transaction, never execution.
            if self.engine.dialect.name == "postgresql":
                db.execute(text("SELECT pg_advisory_xact_lock(704001726)"))
            elif self.engine.dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            else:
                raise QueueConfiguration("Execution queue requires SQLite or PostgreSQL.")
            record = db.scalar(
                select(Verification).where(
                    Verification.id == verification_id, Verification.owner_id == owner_id
                )
            )
            if record is None:
                return None, False
            if key:
                existing = db.scalar(
                    select(ExecutionJob).where(
                        ExecutionJob.owner_id == owner_id, ExecutionJob.idempotency_key == key
                    )
                )
                if existing:
                    self._same_request(existing, verification_id, profile.id)
                    return existing, False
            if record.status != "ready" or not record.snapshot:
                raise StoreConflict("Capture PR evidence before starting execution.")
            if profile.repository.casefold() != record.repository.casefold():
                raise StoreConflict("This test profile does not cover the submitted repository.")
            if db.scalar(select(ExecutionJob.id).where(ExecutionJob.active_key == verification_id)):
                raise StoreConflict(
                    "An execution is already queued or running for this verification."
                )
            active = ExecutionJob.status.in_(("queued", "running"))
            account_count = db.scalar(
                select(func.count())
                .select_from(ExecutionJob)
                .where(active, ExecutionJob.owner_id == owner_id)
            )
            if account_count >= account_limit:
                raise QueueCapacity(
                    "Your account's execution queue is full. "
                    "Wait for a run or cancel a queued execution."
                )
            total_count = db.scalar(select(func.count()).select_from(ExecutionJob).where(active))
            if total_count >= total_limit:
                raise QueueCapacity("The execution queue is full. Please try again later.")
            job = ExecutionJob(
                id=str(uuid4()),
                verification_id=verification_id,
                owner_id=owner_id,
                idempotency_key=key,
                active_key=verification_id,
                profile_id=profile.id,
                profile_snapshot=profile.model_dump(mode="json"),
                image_id=image_id,
                base_sha=record.snapshot["base_sha"],
                head_sha=record.snapshot["head_sha"],
                diff_sha256=record.snapshot["diff_sha256"],
                status="queued",
                stage="queued",
                cancel_requested=False,
                created_at=now,
                updated_at=now,
            )
            try:
                db.add(job)
                db.commit()
                return job, True
            except IntegrityError:
                db.rollback()
                existing = (
                    db.scalar(
                        select(ExecutionJob).where(
                            ExecutionJob.owner_id == owner_id, ExecutionJob.idempotency_key == key
                        )
                    )
                    if key
                    else None
                )
                if existing:
                    self._same_request(existing, verification_id, profile.id)
                    return existing, False
                active = db.scalar(
                    select(ExecutionJob.id).where(ExecutionJob.active_key == verification_id)
                )
                if active:
                    raise StoreConflict(
                        "An execution is already queued or running for this verification."
                    ) from None
                raise

    @staticmethod
    def _same_request(job, verification_id, profile_id):
        if job.verification_id != verification_id or job.profile_id != profile_id:
            raise StoreConflict("This idempotency key was used for a different execution request.")

    def reap(self):
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            db.execute(
                update(ExecutionJob)
                .where(ExecutionJob.status == "running", ExecutionJob.lease_until < now)
                .values(
                    status="failed",
                    stage="failed",
                    active_key=None,
                    claim_token=None,
                    lease_until=None,
                    failure={
                        "code": "worker_lost",
                        "message": "The execution worker stopped responding. Start a new run.",
                    },
                    updated_at=now,
                    finished_at=now,
                )
            )
            db.commit()

    def claim(self):
        self.reap()
        for _ in range(3):
            now = datetime.now(UTC)
            token = str(uuid4())
            with Session(self.engine, expire_on_commit=False) as db:
                identifier = db.scalar(
                    select(ExecutionJob.id)
                    .where(ExecutionJob.status == "queued")
                    .order_by(ExecutionJob.created_at, ExecutionJob.id)
                    .limit(1)
                )
                if identifier is None:
                    return None
                changed = db.execute(
                    update(ExecutionJob)
                    .where(ExecutionJob.id == identifier, ExecutionJob.status == "queued")
                    .values(
                        status="running",
                        stage="checkout",
                        claim_token=token,
                        lease_until=now + timedelta(seconds=90),
                        updated_at=now,
                    )
                )
                if changed.rowcount != 1:
                    db.rollback()
                    continue
                db.commit()
                return db.get(ExecutionJob, identifier)
        return None

    def renew(self, identifier, token):
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            changed = db.execute(
                update(ExecutionJob)
                .where(
                    ExecutionJob.id == identifier,
                    ExecutionJob.claim_token == token,
                    ExecutionJob.status == "running",
                    ExecutionJob.lease_until > now,
                )
                .values(lease_until=now + timedelta(seconds=90), updated_at=now)
            )
            if changed.rowcount != 1:
                raise StoreConflict("Execution lease was lost.")
            cancelled = db.scalar(
                select(ExecutionJob.cancel_requested).where(ExecutionJob.id == identifier)
            )
            db.commit()
            return cancelled

    def progress(self, identifier, token, stage):
        with Session(self.engine) as db:
            changed = db.execute(
                update(ExecutionJob)
                .where(
                    ExecutionJob.id == identifier,
                    ExecutionJob.claim_token == token,
                    ExecutionJob.status == "running",
                )
                .values(stage=stage, updated_at=datetime.now(UTC))
            )
            if changed.rowcount != 1:
                raise StoreConflict("Execution lease was lost.")
            db.commit()

    def finish(self, identifier, token, *, artifact=None, failure=None):
        if bool(artifact) == bool(failure):
            raise ValueError("Execution completion requires evidence or a failure.")
        now = datetime.now(UTC)
        cancelled = ExecutionJob.cancel_requested.is_(True)
        status = "failed" if failure else "completed"
        with Session(self.engine) as db:
            changed = db.execute(
                update(ExecutionJob)
                .where(
                    ExecutionJob.id == identifier,
                    ExecutionJob.claim_token == token,
                    ExecutionJob.status == "running",
                    ExecutionJob.lease_until > now,
                )
                .values(
                    status=case((cancelled, "cancelled"), else_=status),
                    stage=case((cancelled, "cancelled"), else_=status),
                    artifact=case((cancelled, literal(None, JSON)), else_=literal(artifact, JSON)),
                    assessment=case(
                        (cancelled, literal(None, JSON)),
                        else_=literal(artifact.get("assessment") if artifact else None, JSON),
                    ),
                    failure=case((cancelled, literal(None, JSON)), else_=literal(failure, JSON)),
                    active_key=None,
                    claim_token=None,
                    lease_until=None,
                    updated_at=now,
                    finished_at=now,
                )
            )
            if changed.rowcount != 1:
                raise StoreConflict("A stale worker cannot finish this execution.")
            db.commit()

    def cancel(self, identifier, owner_id):
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            db.execute(
                update(ExecutionJob)
                .where(
                    ExecutionJob.id == identifier,
                    ExecutionJob.owner_id == owner_id,
                    ExecutionJob.status == "queued",
                )
                .values(
                    status="cancelled",
                    stage="cancelled",
                    cancel_requested=True,
                    active_key=None,
                    updated_at=now,
                    finished_at=now,
                )
            )
            db.execute(
                update(ExecutionJob)
                .where(
                    ExecutionJob.id == identifier,
                    ExecutionJob.owner_id == owner_id,
                    ExecutionJob.status == "running",
                )
                .values(cancel_requested=True, updated_at=now)
            )
            db.commit()
        return self.get(identifier, owner_id)

    def worker_heartbeat(self, worker_id):
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            worker = db.get(WorkerHeartbeat, worker_id)
            if worker is None:
                db.add(WorkerHeartbeat(id=worker_id, last_seen=now))
            else:
                worker.last_seen = now
            db.execute(
                delete(WorkerHeartbeat).where(WorkerHeartbeat.last_seen < now - timedelta(days=1))
            )
            db.commit()

    def worker_stopped(self, worker_id):
        with Session(self.engine) as db:
            db.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.id == worker_id))
            db.commit()

    def worker_online(self):
        with Session(self.engine) as db:
            return (
                db.scalar(
                    select(WorkerHeartbeat.id)
                    .where(WorkerHeartbeat.last_seen > datetime.now(UTC) - timedelta(seconds=30))
                    .limit(1)
                )
                is not None
            )

    def namespace(self):
        with Session(self.engine) as db:
            value = db.scalar(select(ExecutionNamespace.value).where(ExecutionNamespace.id == 1))
            if value is None:
                raise RuntimeError("Execution namespace migration is required.")
            return value

    def claim_is_live(self, identifier, token):
        with Session(self.engine) as db:
            return (
                db.scalar(
                    select(ExecutionJob.id).where(
                        ExecutionJob.id == identifier,
                        ExecutionJob.claim_token == token,
                        ExecutionJob.status == "running",
                        ExecutionJob.lease_until > datetime.now(UTC),
                    )
                )
                is not None
            )
