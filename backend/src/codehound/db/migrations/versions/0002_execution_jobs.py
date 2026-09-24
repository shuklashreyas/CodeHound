"""Create durable execution jobs and worker heartbeats."""

import sqlalchemy as sa
from alembic import op

revision = "0002_execution_jobs"
down_revision = "0001_verifications"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "execution_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "verification_id", sa.String(36), sa.ForeignKey("verifications.id"), nullable=False
        ),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(36)),
        sa.Column("active_key", sa.String(36)),
        sa.Column("profile_id", sa.String(80), nullable=False),
        sa.Column("profile_snapshot", sa.JSON(), nullable=False),
        sa.Column("image_id", sa.String(71), nullable=False),
        sa.Column("base_sha", sa.String(40), nullable=False),
        sa.Column("head_sha", sa.String(40), nullable=False),
        sa.Column("diff_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(80), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("claim_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("artifact", sa.JSON()),
        sa.Column("assessment", sa.JSON()),
        sa.Column("failure", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="valid_execution_status",
        ),
        sa.UniqueConstraint("active_key", name="uq_active_execution"),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_execution_idempotency"),
    )
    op.create_index("ix_execution_queue", "execution_jobs", ["status", "created_at"])
    op.create_index(
        "ix_execution_verification", "execution_jobs", ["verification_id", "created_at"]
    )
    op.create_table(
        "worker_heartbeats",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("worker_heartbeats")
    op.drop_index("ix_execution_verification", table_name="execution_jobs")
    op.drop_index("ix_execution_queue", table_name="execution_jobs")
    op.drop_table("execution_jobs")
