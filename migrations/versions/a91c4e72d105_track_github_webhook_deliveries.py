"""track GitHub webhook delivery IDs for idempotency

Revision ID: a91c4e72d105
Revises: d47a2c18b903
Create Date: 2026-11-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a91c4e72d105"
down_revision: str | Sequence[str] | None = "d47a2c18b903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "github_webhook_deliveries",
        sa.Column("delivery_id", sa.String(length=128), nullable=False),
        sa.Column("event", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("delivery_id"),
    )
    op.create_index(
        "ix_github_webhook_deliveries_status",
        "github_webhook_deliveries",
        ["status"],
    )
    op.create_index(
        "ix_github_webhook_deliveries_received_at",
        "github_webhook_deliveries",
        ["received_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_github_webhook_deliveries_received_at",
        table_name="github_webhook_deliveries",
    )
    op.drop_index(
        "ix_github_webhook_deliveries_status",
        table_name="github_webhook_deliveries",
    )
    op.drop_table("github_webhook_deliveries")
