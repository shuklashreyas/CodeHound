from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Verification(Base):
    __tablename__ = "verifications"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'intaking', 'ready', 'failed')", name="valid_status"),
        UniqueConstraint("owner_id", "idempotency_key", name="uq_owner_idempotency"),
        Index("ix_verifications_owner_created", "owner_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_login: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(36))
    repository: Mapped[str] = mapped_column(String(201), nullable=False)
    pr_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pr_url: Mapped[str] = mapped_column(String(512), nullable=False)
    issue_text: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    snapshot: Mapped[dict | None] = mapped_column(JSON)
    failure: Mapped[dict | None] = mapped_column(JSON)
    attempts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    claim_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExecutionJob(Base):
    __tablename__ = "execution_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="valid_execution_status",
        ),
        UniqueConstraint("active_key", name="uq_active_execution"),
        UniqueConstraint("owner_id", "idempotency_key", name="uq_execution_idempotency"),
        Index("ix_execution_queue", "status", "created_at"),
        Index("ix_execution_verification", "verification_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    verification_id: Mapped[str] = mapped_column(ForeignKey("verifications.id"), nullable=False)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(36))
    active_key: Mapped[str | None] = mapped_column(String(36))
    profile_id: Mapped[str] = mapped_column(String(80), nullable=False)
    profile_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    image_id: Mapped[str] = mapped_column(String(71), nullable=False)
    base_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    diff_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    stage: Mapped[str] = mapped_column(String(80), nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    claim_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    artifact: Mapped[dict | None] = mapped_column(JSON)
    assessment: Mapped[dict | None] = mapped_column(JSON)
    failure: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExecutionNamespace(Base):
    __tablename__ = "execution_namespace"
    __table_args__ = (CheckConstraint("id = 1", name="single_execution_namespace"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
