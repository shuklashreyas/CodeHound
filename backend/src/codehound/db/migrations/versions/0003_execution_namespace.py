"""Give each database a stable namespace for its disposable worker resources."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0003_execution_namespace"
down_revision = "0002_execution_jobs"
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table(
        "execution_namespace",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("value", sa.String(36), nullable=False, unique=True),
        sa.CheckConstraint("id = 1", name="single_execution_namespace"),
    )
    op.bulk_insert(table, [{"id": 1, "value": str(uuid4())}])


def downgrade():
    op.drop_table("execution_namespace")
