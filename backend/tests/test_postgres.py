"""Opt-in integration test for an isolated PostgreSQL database supplied by the test runner."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from codehound.db.database import Database
from codehound.db.store import StoreConflict, VerificationStore
from codehound.evaluation.schemas import VerificationCreate


@pytest.mark.skipif(
    not os.getenv("CODEHOUND_TEST_POSTGRES_URL"), reason="Isolated PostgreSQL not configured"
)
def test_postgresql_migration_persistence_and_claims():
    database = Database(os.environ["CODEHOUND_TEST_POSTGRES_URL"])
    try:
        database.migrate()
        database.migrate()  # Applying an up-to-date schema is idempotent.
        store = VerificationStore(database)
        owner = uuid4().int % (2**62)
        body = VerificationCreate(
            pr_url="https://github.com/octocat/project/pull/7",
            issue_text="Verify the refresh token behavior.",
        )
        key = str(uuid4())
        created, fresh = store.create(owner, "pg-test", body, key)
        repeated, fresh_again = store.create(owner, "pg-test", body, key)
        assert fresh and not fresh_again and repeated.id == created.id
        assert store.list(owner, 10, 0)[0].id == created.id
        assert store.get(created.id, owner + 1) is None

        def claim():
            try:
                return store.claim(created.id, owner)[1]
            except StoreConflict:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: claim(), range(2)))
        assert sum(c is not None for c in claims) == 1
        winner = next(c for c in claims if c)
        result = store.finish(
            created.id, winner, failure={"code": "test", "message": "Expected failure"}
        )
        assert result.status == "failed"
        reopened = Database(os.environ["CODEHOUND_TEST_POSTGRES_URL"])
        try:
            assert (
                VerificationStore(reopened).get(created.id, owner).attempts[0]["status"] == "failed"
            )
        finally:
            reopened.close()
    finally:
        database.close()


@pytest.mark.skipif(
    not os.getenv("CODEHOUND_TEST_POSTGRES_URL"), reason="Isolated PostgreSQL not configured"
)
def test_postgresql_execution_cancellation_and_json_evidence():
    from codehound.db.jobs import JobStore
    from codehound.evaluation.registry import load_profiles

    database = Database(os.environ["CODEHOUND_TEST_POSTGRES_URL"])
    try:
        database.migrate()
        store, jobs = VerificationStore(database), JobStore(database)
        owner = uuid4().int % (2**62)
        body = VerificationCreate(
            pr_url="https://github.com/shuklashreyas/CodeHound/pull/1",
            issue_text="Check public URL parsing independently.",
        )
        record, _ = store.create(owner, "pg-test", body)
        _, token = store.claim(record.id, owner)
        store.finish(
            record.id,
            token,
            snapshot={
                "pull_request": {"title": "Contract check"},
                "base_sha": "a" * 40,
                "head_sha": "b" * 40,
                "diff_sha256": "c" * 64,
            },
        )
        profile = load_profiles()["codehound-url-contract"]
        job, _ = jobs.enqueue(record.id, owner, profile, "sha256:" + "d" * 64)
        claimed = jobs.claim()
        assert claimed.id == job.id
        jobs.cancel(job.id, owner)
        jobs.finish(
            job.id, claimed.claim_token, artifact={"assessment": {"verdict": "candidate_improves"}}
        )
        result = jobs.get(job.id, owner)
        assert (
            result.status == "cancelled" and result.artifact is None and result.assessment is None
        )
        fresh, _ = jobs.enqueue(record.id, owner, profile, "sha256:" + "d" * 64)
        claimed = jobs.claim()
        evidence = {"assessment": {"verdict": "incomplete"}, "suites": {}}
        jobs.finish(fresh.id, claimed.claim_token, artifact=evidence)
        assert jobs.get(fresh.id, owner).artifact == evidence
    finally:
        database.close()
