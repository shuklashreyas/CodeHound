"""Create durable verification submissions.

Revision ID: 0001_verifications
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_verifications"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "verifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_login", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(36)),
        sa.Column("repository", sa.String(201), nullable=False),
        sa.Column("pr_number", sa.BigInteger(), nullable=False),
        sa.Column("pr_url", sa.String(512), nullable=False),
        sa.Column("issue_text", sa.Text(), nullable=False),
        sa.Column("title", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("snapshot", sa.JSON()),
        sa.Column("failure", sa.JSON()),
        sa.Column("attempts", sa.JSON(), nullable=False),
        sa.Column("claim_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'intaking', 'ready', 'failed')", name="valid_status"
        ),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_owner_idempotency"),
    )
    op.create_index(
        "ix_verifications_owner_created", "verifications", ["owner_id", "created_at", "id"]
    )


def downgrade():
    op.drop_index("ix_verifications_owner_created", table_name="verifications")
    op.drop_table("verifications")
