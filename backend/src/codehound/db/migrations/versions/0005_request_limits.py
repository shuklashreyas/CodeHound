"""Bound shared request windows independently of execution queue capacity."""

import sqlalchemy as sa
from alembic import op

revision = "0005_request_limits"
down_revision = "0004_auth_store"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "request_limit_buckets",
        sa.Column("bucket", sa.String(16), primary_key=True),
        sa.Column("principal_sha256", sa.String(64), primary_key=True),
        sa.Column("window_start", sa.BigInteger(), nullable=False),
        sa.Column("window_end", sa.BigInteger(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.CheckConstraint("requests >= 0", name="nonnegative_request_count"),
        sa.CheckConstraint("window_end > window_start", name="valid_request_window"),
    )
    op.create_index("ix_request_limit_expiry", "request_limit_buckets", ["window_end"])


def downgrade():
    op.drop_table("request_limit_buckets")
