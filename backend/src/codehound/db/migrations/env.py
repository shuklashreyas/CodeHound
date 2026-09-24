from alembic import context

from codehound.db.models import Base

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Run migrations through python -m codehound.db.")
context.configure(connection=connection, target_metadata=Base.metadata)
with context.begin_transaction():
    context.run_migrations()
