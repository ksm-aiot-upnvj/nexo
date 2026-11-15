"""add durable webhook outbox jobs

Revision ID: b2c4e91d7a60
Revises: a91c4e72d105
Create Date: 2026-11-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c4e91d7a60"
down_revision: str | Sequence[str] | None = "a91c4e72d105"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "webhook_outbox_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("delivery_id", sa.String(length=128), nullable=False),
        sa.Column("effect_key", sa.String(length=64), nullable=False),
        sa.Column("job_type", sa.String(length=50), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "delivery_id", "effect_key", name="uq_webhook_outbox_delivery_effect"
        ),
    )
    op.create_index(
        "ix_webhook_outbox_jobs_delivery_id",
        "webhook_outbox_jobs",
        ["delivery_id"],
    )
    op.create_index(
        "ix_webhook_outbox_jobs_status",
        "webhook_outbox_jobs",
        ["status"],
    )
    op.create_index(
        "ix_webhook_outbox_status_available",
        "webhook_outbox_jobs",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_webhook_outbox_status_lease",
        "webhook_outbox_jobs",
        ["status", "locked_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_outbox_status_lease", table_name="webhook_outbox_jobs")
    op.drop_index(
        "ix_webhook_outbox_status_available", table_name="webhook_outbox_jobs"
    )
    op.drop_index("ix_webhook_outbox_jobs_status", table_name="webhook_outbox_jobs")
    op.drop_index(
        "ix_webhook_outbox_jobs_delivery_id", table_name="webhook_outbox_jobs"
    )
    op.drop_table("webhook_outbox_jobs")
