"""Bounded shared fixed-window request counters for SQLite and PostgreSQL."""

import hashlib
import os
import re
import time
from dataclasses import dataclass

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Index,
    Integer,
    String,
    delete,
    func,
    select,
    text,
    tuple_,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from codehound.db.models import Base, ExecutionNamespace

PRUNE_BATCH = 200


class RequestLimitConfiguration(Exception):
    pass


class RequestLimitStorageFull(Exception):
    pass


class RequestLimitBucket(Base):
    __tablename__ = "request_limit_buckets"
    __table_args__ = (
        CheckConstraint("requests >= 0", name="nonnegative_request_count"),
        CheckConstraint("window_end > window_start", name="valid_request_window"),
        Index("ix_request_limit_expiry", "window_end"),
    )

    bucket: Mapped[str] = mapped_column(String(16), primary_key=True)
    principal_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[int] = mapped_column(BigInteger, nullable=False)
    window_end: Mapped[int] = mapped_column(BigInteger, nullable=False)
    requests: Mapped[int] = mapped_column(Integer, nullable=False)


@dataclass(frozen=True)
class RequestLimitSettings:
    mutation_limit: int
    oauth_limit: int
    window_seconds: int
    max_buckets: int

    def __post_init__(self):
        for value, maximum in (
            (self.mutation_limit, 10000),
            (self.oauth_limit, 1000),
            (self.window_seconds, 3600),
            (self.max_buckets, 1000000),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise RequestLimitConfiguration("Request rate limits are misconfigured.")


@dataclass(frozen=True)
class RequestLimitDecision:
    allowed: bool
    retry_after: int
    remaining: int


def rate_limit_settings():
    values = []
    for key, default, maximum in (
        ("CODEHOUND_MUTATION_REQUEST_LIMIT", "120", 10000),
        ("CODEHOUND_OAUTH_REQUEST_LIMIT", "20", 1000),
        ("CODEHOUND_REQUEST_LIMIT_WINDOW_SECONDS", "60", 3600),
        ("CODEHOUND_REQUEST_LIMIT_MAX_BUCKETS", "100000", 1000000),
    ):
        raw = os.getenv(key, default)
        if not re.fullmatch(r"[1-9][0-9]{0,6}", raw) or int(raw) > maximum:
            raise RequestLimitConfiguration("Request rate limits are misconfigured.")
        values.append(int(raw))
    return RequestLimitSettings(*values)


def principal_digest(namespace, bucket, identity):
    """Salt identities per database; retain neither raw owner IDs nor IPs."""
    payload = f"codehound-request-v1\0{namespace}\0{bucket}\0{identity}".encode()
    return hashlib.sha256(payload).hexdigest()


class RequestLimitStore:
    def __init__(self, database):
        self.engine = database.engine

    def consume(self, bucket, identity, *, settings=None, now=None):
        settings = settings or rate_limit_settings()
        if (
            bucket not in ("mutation", "oauth")
            or not isinstance(identity, str)
            or not 1 <= len(identity) <= 200
        ):
            raise ValueError("A bounded internal request principal is required.")
        now = int(time.time()) if now is None else now
        if type(now) is not int or now < 0:
            raise ValueError("Request window time must be a nonnegative integer.")
        limit = settings.mutation_limit if bucket == "mutation" else settings.oauth_limit
        start = now // settings.window_seconds * settings.window_seconds
        end = start + settings.window_seconds
        with Session(self.engine) as db:
            # This short admission transaction serializes both counting and the
            # global bucket cap across processes. It holds no network operation.
            if self.engine.dialect.name == "postgresql":
                db.execute(text("SELECT pg_advisory_xact_lock(704001730)"))
            elif self.engine.dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            else:
                raise RequestLimitConfiguration("Request limits require SQLite or PostgreSQL.")
            stale = list(
                db.execute(
                    select(RequestLimitBucket.bucket, RequestLimitBucket.principal_sha256)
                    .where(RequestLimitBucket.window_end <= now)
                    .order_by(RequestLimitBucket.window_end)
                    .limit(PRUNE_BATCH)
                )
            )
            if stale:
                db.execute(
                    delete(RequestLimitBucket).where(
                        tuple_(RequestLimitBucket.bucket, RequestLimitBucket.principal_sha256).in_(
                            stale
                        )
                    )
                )
            namespace = db.scalar(
                select(ExecutionNamespace.value).where(ExecutionNamespace.id == 1)
            )
            if namespace is None:
                raise RequestLimitConfiguration("The database request namespace is unavailable.")
            digest = principal_digest(namespace, bucket, identity)
            current = db.get(RequestLimitBucket, (bucket, digest))
            if current is None:
                count = db.scalar(select(func.count()).select_from(RequestLimitBucket))
                if count >= settings.max_buckets:
                    # Commit any bounded pruning even when new admission fails.
                    db.commit()
                    raise RequestLimitStorageFull("Request limit storage is full.")
                current = RequestLimitBucket(
                    bucket=bucket,
                    principal_sha256=digest,
                    window_start=start,
                    window_end=end,
                    requests=0,
                )
                db.add(current)
            elif (current.window_start, current.window_end) != (start, end):
                current.window_start, current.window_end, current.requests = start, end, 0
            allowed = current.requests < limit
            if allowed:
                current.requests += 1
            remaining = max(0, limit - current.requests)
            db.commit()
            return RequestLimitDecision(allowed, max(1, end - now), remaining)
