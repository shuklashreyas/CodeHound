import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import StaticPool


def default_url():
    root = Path(__file__).resolve().parents[4]
    if not (root / "backend" / "pyproject.toml").is_file():
        root = Path.cwd()
    path = Path(os.getenv("CODEHOUND_DATA_DIR", str(root / "data"))) / "codehound.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


class Database:
    def __init__(self, url: str | None = None):
        url = url or os.getenv("CODEHOUND_DATABASE_URL")
        if not url and os.getenv("CODEHOUND_POSTGRES_HOST"):
            url = URL.create(
                "postgresql+psycopg",
                username=os.getenv("POSTGRES_USER", "codehound"),
                password=os.getenv("POSTGRES_PASSWORD", "codehound_local"),
                host=os.environ["CODEHOUND_POSTGRES_HOST"],
                port=5432,
                database=os.getenv("POSTGRES_DB", "codehound"),
            )
        url = url or default_url()
        options = {"pool_pre_ping": True}
        if make_url(url).get_backend_name() == "postgresql":
            options["connect_args"] = {"connect_timeout": 5}
        if make_url(url).get_backend_name() == "sqlite":
            options["connect_args"] = {"check_same_thread": False, "timeout": 10}
            if url in ("sqlite://", "sqlite:///:memory:"):
                options["poolclass"] = StaticPool
        self.engine = create_engine(url, **options)

    def migrate(self):
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        with self.engine.begin() as connection:
            if self.engine.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_xact_lock(704001725)"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    def close(self):
        self.engine.dispose()
