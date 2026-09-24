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
