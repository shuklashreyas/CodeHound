from sqlalchemy import inspect

from codehound.db.database import Database


def test_postgres_password_is_not_interpolated_into_a_url(monkeypatch):
    monkeypatch.delenv("CODEHOUND_DATABASE_URL", raising=False)
    monkeypatch.setenv("CODEHOUND_POSTGRES_HOST", "db")
    monkeypatch.setenv("POSTGRES_PASSWORD", "has:@/percent%and#symbols")
    database = Database()
    try:
        assert database.engine.url.password == "has:@/percent%and#symbols"
        assert database.engine.url.host == "db"
    finally:
        database.close()


def test_migration_schema_and_repeated_upgrade(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'database.db'}")
    try:
        database.migrate()
        database.migrate()
        inspector = inspect(database.engine)
        assert "verifications" in inspector.get_table_names()
        assert "alembic_version" in inspector.get_table_names()
        assert any(
            index["name"] == "ix_verifications_owner_created"
            for index in inspector.get_indexes("verifications")
        )
    finally:
        database.close()


def test_api_and_worker_can_migrate_sqlite_concurrently(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    url = f"sqlite:///{tmp_path / 'shared.db'}"

    def migrate():
        database = Database(url)
        try:
            database.migrate()
            return set(inspect(database.engine).get_table_names())
        finally:
            database.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: migrate(), range(2)))
    assert all(
        {"verifications", "execution_jobs", "worker_heartbeats"} <= names for names in results
    )
