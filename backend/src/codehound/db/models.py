from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
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
