"""Add durable owner-scoped change-feed cursors.

Revision ID: 0007_sync_feed_cursors
Revises: 0006_merge_sync_and_device_state
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_sync_feed_cursors"
down_revision = "0006_merge_sync_and_device_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_feed_cursors",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.CHAR(length=26), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index(
        "ix_sync_feed_cursors_owner_expiry",
        "sync_feed_cursors",
        ["owner_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_sync_feed_cursors_owner_expiry", table_name="sync_feed_cursors")
    op.drop_table("sync_feed_cursors")
