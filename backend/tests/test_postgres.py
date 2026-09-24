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
