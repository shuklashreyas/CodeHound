"""Encrypted shared OAuth flows and sessions with opaque hashed lookup IDs."""

import sqlalchemy as sa
from alembic import op

revision = "0004_auth_store"
down_revision = "0003_execution_namespace"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "auth_records",
        sa.Column("kind", sa.String(10), primary_key=True),
        sa.Column("id_hash", sa.String(64), primary_key=True),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("expires_ms", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("kind IN ('flow', 'session')", name="valid_auth_kind"),
    )
    op.create_index("ix_auth_records_expiry", "auth_records", ["expires_ms"])
    op.create_table(
        "auth_store_key",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.CheckConstraint("id = 1", name="single_auth_store_key"),
    )


def downgrade():
    op.drop_table("auth_store_key")
    op.drop_index("ix_auth_records_expiry", table_name="auth_records")
    op.drop_table("auth_records")
