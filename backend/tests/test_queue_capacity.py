import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from codehound.db.database import Database
from codehound.db.jobs import JobStore, QueueCapacity, QueueConfiguration
from codehound.db.models import ExecutionJob
from codehound.db.store import VerificationStore
from codehound.evaluation.registry import load_profiles
from codehound.evaluation.schemas import VerificationCreate

IMAGE = "sha256:" + "a" * 64


def ready_record(database, owner=123):
    records = VerificationStore(database)
    record, _ = records.create(
        owner,
        "quota-test",
        VerificationCreate(
            pr_url="https://github.com/shuklashreyas/CodeHound/pull/1",
            issue_text="Check the URL parsing contract independently.",
        ),
    )
    _, token = records.claim(record.id, owner)
    records.finish(
        record.id,
        token,
        snapshot={
            "pull_request": {"title": "Quota test"},
            "base_sha": "a" * 40,
            "head_sha": "b" * 40,
            "diff_sha256": "c" * 64,
        },
    )
    return record.id


@pytest.fixture
def database():
    database = Database()
    database.migrate()
    yield database
    database.close()


def test_capacity_idempotency_cancellation_and_other_accounts(database, monkeypatch):
    monkeypatch.setenv("CODEHOUND_MAX_PENDING_PER_ACCOUNT", "1")
    monkeypatch.setenv("CODEHOUND_MAX_PENDING_EXECUTIONS", "2")
    store, profile = JobStore(database), load_profiles()["codehound-url-contract"]
    first, second, third = [ready_record(database) for _ in range(3)]
    key = str(uuid4())
    job, created = store.enqueue(first, 123, profile, IMAGE, key)
    assert created
    again, created = store.enqueue(first, 123, profile, IMAGE, key)
    assert again.id == job.id and not created
    with pytest.raises(QueueCapacity, match="account"):
        store.enqueue(second, 123, profile, IMAGE)
    another = ready_record(database, 456)
    store.enqueue(another, 456, profile, IMAGE)
    with pytest.raises(QueueCapacity, match="queue is full"):
        store.enqueue(ready_record(database, 789), 789, profile, IMAGE)
    store.cancel(job.id, 123)
    assert store.enqueue(third, 123, profile, IMAGE)[1]


def test_concurrent_sqlite_admission_does_not_exceed_capacity(database, monkeypatch):
    monkeypatch.setenv("CODEHOUND_MAX_PENDING_PER_ACCOUNT", "2")
    store, profile = JobStore(database), load_profiles()["codehound-url-contract"]
    records = [ready_record(database) for _ in range(8)]

    def submit(identifier):
        try:
            store.enqueue(identifier, 123, profile, IMAGE)
            return True
        except QueueCapacity:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(submit, records)) == 2
    with Session(database.engine) as session:
        assert session.scalar(select(func.count()).select_from(ExecutionJob)) == 2


def test_invalid_capacity_configuration_fails_closed(database, monkeypatch):
    record = ready_record(database)
    store, profile = JobStore(database), load_profiles()["codehound-url-contract"]
    for value in ("0", "-1", "unlimited", "100000"):
        monkeypatch.setenv("CODEHOUND_MAX_PENDING_EXECUTIONS", value)
        with pytest.raises(QueueConfiguration):
            store.enqueue(record, 123, profile, IMAGE)
    with Session(database.engine) as session:
        assert session.scalar(select(func.count()).select_from(ExecutionJob)) == 0


@pytest.mark.skipif(
    not os.getenv("CODEHOUND_TEST_POSTGRES_URL"), reason="Isolated PostgreSQL required"
)
def test_concurrent_postgres_admission_does_not_exceed_capacity(monkeypatch):
    monkeypatch.setenv("CODEHOUND_MAX_PENDING_PER_ACCOUNT", "2")
    monkeypatch.setenv("CODEHOUND_MAX_PENDING_EXECUTIONS", "1000")
    database = Database(os.environ["CODEHOUND_TEST_POSTGRES_URL"])
    owner = uuid4().int % (2**62)
    store, profile = JobStore(database), load_profiles()["codehound-url-contract"]
    try:
        database.migrate()
        records = [ready_record(database, owner) for _ in range(8)]

        def submit(identifier):
            try:
                store.enqueue(identifier, owner, profile, IMAGE)
                return True
            except QueueCapacity:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            assert sum(pool.map(submit, records)) == 2
        with Session(database.engine) as session:
            jobs = list(session.scalars(select(ExecutionJob).where(ExecutionJob.owner_id == owner)))
            assert len(jobs) == 2
        for job in jobs:
            store.cancel(job.id, owner)
    finally:
        database.close()


def test_global_capacity_is_atomic_across_accounts(database, monkeypatch):
    monkeypatch.setenv("CODEHOUND_MAX_PENDING_PER_ACCOUNT", "100")
    monkeypatch.setenv("CODEHOUND_MAX_PENDING_EXECUTIONS", "2")
    store, profile = JobStore(database), load_profiles()["codehound-url-contract"]
    submissions = [(ready_record(database, owner), owner) for owner in range(100, 108)]

    def submit(item):
        identifier, owner = item
        try:
            store.enqueue(identifier, owner, profile, IMAGE)
            return True
        except QueueCapacity:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(submit, submissions)) == 2
