import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from codehound.db.database import Database
from codehound.db.jobs import JobStore
from codehound.db.models import ExecutionJob, Verification
from codehound.db.storage_limits import (
    StorageCapacity,
    StorageConfiguration,
    StorageLimits,
    storage_limits,
)
from codehound.db.store import StoreConflict, VerificationStore
from codehound.evaluation.registry import load_profiles
from codehound.evaluation.schemas import VerificationCreate

IMAGE = "sha256:" + "a" * 64
HEADERS = {"X-CodeHound-Request": "1"}


def body(**changes):
    return VerificationCreate(
        **{
            "pr_url": "https://github.com/shuklashreyas/CodeHound/pull/1",
            "issue_text": "Verify the public URL parsing behavior independently.",
            **changes,
        }
    )


def ready_record(database, owner=123):
    store = VerificationStore(database)
    item, _ = store.create(owner, "quota-test", body())
    _, token = store.claim(item.id, owner)
    store.finish(
        item.id,
        token,
        snapshot={
            "pull_request": {"title": "Contract check"},
            "base_sha": "a" * 40,
            "head_sha": "b" * 40,
            "diff_sha256": "c" * 64,
        },
    )
    return item.id


@pytest.fixture
def database():
    database = Database()
    database.migrate()
    yield database
    database.close()


@pytest.mark.parametrize(
    "key",
    [
        "CODEHOUND_MAX_VERIFICATIONS_PER_ACCOUNT",
        "CODEHOUND_MAX_VERIFICATIONS_TOTAL",
        "CODEHOUND_MAX_EXECUTIONS_PER_ACCOUNT",
        "CODEHOUND_MAX_EXECUTIONS_TOTAL",
    ],
)
@pytest.mark.parametrize("value", ["0", "-1", "", "unlimited", "1.5", " 2", "+2", "01", "1000001"])
def test_storage_configuration_is_strict_and_bounded(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(StorageConfiguration):
        storage_limits()


def test_storage_defaults_and_invalid_internal_limits(monkeypatch):
    for key in (
        "CODEHOUND_MAX_VERIFICATIONS_PER_ACCOUNT",
        "CODEHOUND_MAX_VERIFICATIONS_TOTAL",
        "CODEHOUND_MAX_EXECUTIONS_PER_ACCOUNT",
        "CODEHOUND_MAX_EXECUTIONS_TOTAL",
    ):
        monkeypatch.delenv(key, raising=False)
    assert storage_limits() == StorageLimits(1000, 50000, 1000, 50000)
    with pytest.raises(StorageConfiguration):
        StorageLimits(True, 50000, 1000, 50000)


def test_verification_quota_preserves_idempotency_and_existing_read_access(database, monkeypatch):
    monkeypatch.setenv("CODEHOUND_MAX_VERIFICATIONS_PER_ACCOUNT", "1")
    monkeypatch.setenv("CODEHOUND_MAX_VERIFICATIONS_TOTAL", "2")
    store = VerificationStore(database)
    key = str(uuid4())
    item, created = store.create(123, "quota-test", body(), key)
    repeated, created_again = store.create(123, "quota-test", body(), key)
    assert created and not created_again and repeated.id == item.id
    with pytest.raises(StoreConflict, match="different submission"):
        store.create(123, "quota-test", body(issue_text="Changed submission inputs."), key)
    with pytest.raises(StorageCapacity, match="Your saved verification"):
        store.create(123, "quota-test", body())
    another, _ = store.create(456, "second-owner", body(), key)
    with pytest.raises(StorageCapacity, match="shared saved verification"):
        store.create(789, "third-owner", body())
    assert store.get(item.id, 123).status == "draft"
    assert store.get(item.id, 456) is None
    assert store.list(456, 10, 0)[0].id == another.id
    _, token = store.claim(item.id, 123)
    assert token is not None  # Existing records can still intake at capacity.


def test_completed_failed_and_cancelled_jobs_keep_history_quota(database, monkeypatch):
    monkeypatch.setenv("CODEHOUND_MAX_EXECUTIONS_PER_ACCOUNT", "3")
    store, profile = JobStore(database), load_profiles()["codehound-url-contract"]
    records = [ready_record(database) for _ in range(4)]
    keys = [str(uuid4()) for _ in range(3)]
    completed, _ = store.enqueue(records[0], 123, profile, IMAGE, keys[0])
    claim = store.claim()
    store.finish(
        completed.id, claim.claim_token, artifact={"assessment": {"verdict": "incomplete"}}
    )
    failed, _ = store.enqueue(records[1], 123, profile, IMAGE, keys[1])
    claim = store.claim()
    store.finish(failed.id, claim.claim_token, failure={"code": "test", "message": "Expected"})
    cancelled, _ = store.enqueue(records[2], 123, profile, IMAGE, keys[2])
    store.cancel(cancelled.id, 123)
    with pytest.raises(StorageCapacity, match="execution history"):
        store.enqueue(records[3], 123, profile, IMAGE)
    for record, key, expected in zip(records, keys, (completed, failed, cancelled), strict=False):
        repeated, created = store.enqueue(record, 123, profile, IMAGE, key)
        assert not created and repeated.id == expected.id
    with Session(database.engine) as db:
        assert set(db.scalars(select(ExecutionJob.status))) == {"completed", "failed", "cancelled"}
    assert store.get(completed.id, 123).artifact["assessment"]["verdict"] == "incomplete"


def test_verification_quota_does_not_block_execution_of_existing_ready_record(
    database, monkeypatch
):
    monkeypatch.setenv("CODEHOUND_MAX_VERIFICATIONS_PER_ACCOUNT", "1")
    record = ready_record(database)
    store = JobStore(database)
    job, created = store.enqueue(record, 123, load_profiles()["codehound-url-contract"], IMAGE)
    assert created and job.verification_id == record
    store.cancel(job.id, 123)
    assert store.list(record, 123)[0].status == "cancelled"


@pytest.mark.parametrize("scope", ["owner", "global"])
def test_concurrent_sqlite_verification_admission_is_atomic(database, monkeypatch, scope):
    monkeypatch.setenv(
        "CODEHOUND_MAX_VERIFICATIONS_PER_ACCOUNT", "2" if scope == "owner" else "100"
    )
    monkeypatch.setenv("CODEHOUND_MAX_VERIFICATIONS_TOTAL", "100" if scope == "owner" else "2")
    store = VerificationStore(database)

    def submit(index):
        try:
            store.create(123 if scope == "owner" else 100 + index, "quota-test", body())
            return True
        except StorageCapacity:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(submit, range(8))) == 2
    with Session(database.engine) as db:
        assert db.scalar(select(func.count()).select_from(Verification)) == 2


@pytest.mark.parametrize("scope", ["owner", "global"])
def test_concurrent_sqlite_execution_history_admission_is_atomic(database, monkeypatch, scope):
    monkeypatch.setenv("CODEHOUND_MAX_EXECUTIONS_PER_ACCOUNT", "2" if scope == "owner" else "100")
    monkeypatch.setenv("CODEHOUND_MAX_EXECUTIONS_TOTAL", "100" if scope == "owner" else "2")
    store, profile = JobStore(database), load_profiles()["codehound-url-contract"]
    submissions = [
        (
            ready_record(database, 123 if scope == "owner" else index + 100),
            123 if scope == "owner" else index + 100,
        )
        for index in range(8)
    ]

    def submit(item):
        try:
            store.enqueue(item[0], item[1], profile, IMAGE)
            return True
        except StorageCapacity:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(submit, submissions)) == 2
    with Session(database.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExecutionJob)) == 2


def test_invalid_storage_config_rejects_new_admission_without_new_rows(database, monkeypatch):
    monkeypatch.setenv("CODEHOUND_MAX_VERIFICATIONS_TOTAL", "unlimited")
    with pytest.raises(StorageConfiguration):
        VerificationStore(database).create(123, "quota-test", body())
    with Session(database.engine) as db:
        assert db.scalar(select(func.count()).select_from(Verification)) == 0
    monkeypatch.setenv("CODEHOUND_MAX_VERIFICATIONS_TOTAL", "50000")
    record = ready_record(database)
    monkeypatch.setenv("CODEHOUND_MAX_EXECUTIONS_TOTAL", "0")
    with pytest.raises(StorageConfiguration):
        JobStore(database).enqueue(record, 123, load_profiles()["codehound-url-contract"], IMAGE)
    with Session(database.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExecutionJob)) == 0


def test_api_history_capacity_is_409_without_retry_timer_and_reads_remain_available(
    authenticated_client, monkeypatch
):
    monkeypatch.setenv("CODEHOUND_MAX_VERIFICATIONS_PER_ACCOUNT", "1")
    submission, key = body().model_dump(), str(uuid4())
    headers = HEADERS | {"Idempotency-Key": key}
    created = authenticated_client.post("/api/verifications", json=submission, headers=headers)
    assert created.status_code == 201
    identifier = created.json()["id"]
    denied = authenticated_client.post("/api/verifications", json=submission, headers=HEADERS)
    assert denied.status_code == 409 and "Retry-After" not in denied.headers
    assert "operator" in denied.json()["detail"]
    repeated = authenticated_client.post("/api/verifications", json=submission, headers=headers)
    assert repeated.status_code == 200 and repeated.json()["id"] == identifier
    assert authenticated_client.get(f"/api/verifications/{identifier}").status_code == 200
    assert authenticated_client.get(f"/api/verifications/{identifier}/export").status_code == 200
    monkeypatch.setenv("CODEHOUND_MAX_VERIFICATIONS_PER_ACCOUNT", "unlimited")
    invalid = authenticated_client.post("/api/verifications", json=submission, headers=HEADERS)
    assert invalid.status_code == 503


def test_api_execution_history_counts_cancelled_and_preserves_idempotent_retry(
    authenticated_client, monkeypatch
):
    from codehound.main import app

    monkeypatch.setenv("CODEHOUND_MAX_EXECUTIONS_PER_ACCOUNT", "1")
    monkeypatch.setenv("CODEHOUND_EXECUTION_IMAGE_ID", IMAGE)
    record = ready_record(app.state.database)
    key = str(uuid4())
    path = f"/api/verifications/{record}/executions"
    submission = {"profile_id": "codehound-url-contract"}
    headers = HEADERS | {"Idempotency-Key": key}
    created = authenticated_client.post(path, json=submission, headers=headers)
    assert created.status_code == 202
    job = created.json()["id"]
    assert (
        authenticated_client.post(f"/api/executions/{job}/cancel", headers=HEADERS).status_code
        == 200
    )
    denied = authenticated_client.post(path, json=submission, headers=HEADERS)
    assert denied.status_code == 409 and "Retry-After" not in denied.headers
    repeated = authenticated_client.post(path, json=submission, headers=headers)
    assert repeated.status_code == 200 and repeated.json()["id"] == job
    assert authenticated_client.get(f"/api/executions/{job}").json()["status"] == "cancelled"
    monkeypatch.setenv("CODEHOUND_MAX_EXECUTIONS_TOTAL", "unlimited")
    invalid = authenticated_client.post(path, json=submission, headers=HEADERS)
    assert invalid.status_code == 503


@pytest.mark.skipif(
    not os.getenv("CODEHOUND_TEST_POSTGRES_URL"), reason="Isolated PostgreSQL not configured"
)
@pytest.mark.parametrize("scope", ["owner", "global"])
def test_postgres_verification_and_execution_history_quota_admission_is_atomic(monkeypatch, scope):
    base_url = make_url(os.environ["CODEHOUND_TEST_POSTGRES_URL"])
    schema = "storage_test_" + uuid4().hex
    bootstrap = Database(base_url)
    try:
        with bootstrap.engine.begin() as connection:
            connection.execute(CreateSchema(schema))
    finally:
        bootstrap.close()
    database = Database(base_url.update_query_dict({"options": f"-csearch_path={schema}"}))
    try:
        database.migrate()
        monkeypatch.setenv(
            "CODEHOUND_MAX_VERIFICATIONS_PER_ACCOUNT", "2" if scope == "owner" else "100"
        )
        monkeypatch.setenv("CODEHOUND_MAX_VERIFICATIONS_TOTAL", "100" if scope == "owner" else "2")
        records = VerificationStore(database)

        def create(index):
            try:
                records.create(123 if scope == "owner" else index + 100, "quota-test", body())
                return True
            except StorageCapacity:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            assert sum(pool.map(create, range(8))) == 2
        monkeypatch.setenv("CODEHOUND_MAX_VERIFICATIONS_PER_ACCOUNT", "100")
        monkeypatch.setenv("CODEHOUND_MAX_VERIFICATIONS_TOTAL", "100")
        monkeypatch.setenv(
            "CODEHOUND_MAX_EXECUTIONS_PER_ACCOUNT", "2" if scope == "owner" else "100"
        )
        monkeypatch.setenv("CODEHOUND_MAX_EXECUTIONS_TOTAL", "100" if scope == "owner" else "2")
        jobs, profile = JobStore(database), load_profiles()["codehound-url-contract"]
        submissions = [
            (
                ready_record(database, 123 if scope == "owner" else index + 100),
                123 if scope == "owner" else index + 100,
            )
            for index in range(8)
        ]

        def enqueue(item):
            try:
                jobs.enqueue(item[0], item[1], profile, IMAGE)
                return True
            except StorageCapacity:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            assert sum(pool.map(enqueue, submissions)) == 2
        with Session(database.engine) as db:
            assert db.scalar(select(func.count()).select_from(ExecutionJob)) == 2
    finally:
        with database.engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        database.close()
