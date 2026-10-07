"""Logical retained-record admission bounds, independent of filesystem quotas."""

import os
import re
from dataclasses import dataclass

from sqlalchemy import func, select, text

from codehound.db.models import ExecutionJob, Verification


class StorageCapacity(Exception):
    pass


class StorageConfiguration(Exception):
    pass


@dataclass(frozen=True)
class StorageLimits:
    verifications_per_account: int
    verifications_total: int
    executions_per_account: int
    executions_total: int

    def __post_init__(self):
        if any(
            type(value) is not int or not 1 <= value <= 1000000
            for value in (
                self.verifications_per_account,
                self.verifications_total,
                self.executions_per_account,
                self.executions_total,
            )
        ):
            raise StorageConfiguration("Persisted record limits are misconfigured.")


def storage_limits():
    values = []
    for key, default in (
        ("CODEHOUND_MAX_VERIFICATIONS_PER_ACCOUNT", "1000"),
        ("CODEHOUND_MAX_VERIFICATIONS_TOTAL", "50000"),
        ("CODEHOUND_MAX_EXECUTIONS_PER_ACCOUNT", "1000"),
        ("CODEHOUND_MAX_EXECUTIONS_TOTAL", "50000"),
    ):
        raw = os.getenv(key, default)
        if not re.fullmatch(r"[1-9][0-9]{0,6}", raw) or int(raw) > 1000000:
            raise StorageConfiguration("Persisted record limits are misconfigured.")
        values.append(int(raw))
    return StorageLimits(*values)


def serialize_verification_admission(db):
    """Serialize count plus insert across API processes using the same database."""
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(704001731)"))
    elif dialect == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    else:
        raise StorageConfiguration("Persisted record admission requires SQLite or PostgreSQL.")


def check_record_admission(db, model, owner_id):
    """Caller holds its admission lock until the new row commits or rolls back."""
    limits = storage_limits()
    if model is Verification:
        account_limit, total_limit = limits.verifications_per_account, limits.verifications_total
        label = "saved verification"
    elif model is ExecutionJob:
        account_limit, total_limit = limits.executions_per_account, limits.executions_total
        label = "execution history"
    else:
        raise ValueError("Only saved verification and execution history admission is supported.")
    account_count = db.scalar(
        select(func.count()).select_from(model).where(model.owner_id == owner_id)
    )
    if account_count >= account_limit:
        raise StorageCapacity(
            f"Your {label} limit has been reached. Ask the operator to adjust retained records "
            "or the configured limit before retrying."
        )
    total_count = db.scalar(select(func.count()).select_from(model))
    if total_count >= total_limit:
        raise StorageCapacity(
            f"The shared {label} limit has been reached. The operator must adjust retained "
            "records or the configured limit before new records can be saved."
        )
