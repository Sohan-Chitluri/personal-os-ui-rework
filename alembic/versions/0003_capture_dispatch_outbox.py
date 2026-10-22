"""Add a transactional outbox for capture dispatch.

Revision ID: 0003_capture_dispatch_outbox
Revises: 0002_uniqueness
Create Date: 2026-10-18
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003_capture_dispatch_outbox"
down_revision = "0002_uniqueness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "capture_dispatch_outbox",
        sa.Column("id", sa.CHAR(length=26), primary_key=True, nullable=False),
        sa.Column(
            "capture_id",
            sa.CHAR(length=26),
            sa.ForeignKey("captures.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.UniqueConstraint("capture_id", name="uq_capture_dispatch_outbox_capture_id"),
    )
    op.create_index(
        "ix_capture_dispatch_outbox_state_next_attempt_created",
        "capture_dispatch_outbox",
        ["state", "next_attempt_at", "created_at"],
    )
    # Backfill the new atomic dispatch record for unfinished jobs that predate
    # the outbox. Reuse the capture ULID as the outbox row ID; capture_id is
    # unique so this is deterministic and collision-free within this table.
    op.execute(
        sa.text(
            """
            INSERT INTO capture_dispatch_outbox
                (id, capture_id, state, attempts, created_at, next_attempt_at)
            SELECT c.id, c.id, 'pending', 0, c.created_at, NULL
            FROM captures AS c
            JOIN capture_processing AS p ON p.capture_id = c.id
            WHERE c.deleted_at IS NULL
              AND p.status IN ('pending', 'processing', 'failed')
              AND p.attempt < p.max_attempts
              AND NOT EXISTS (
                  SELECT 1 FROM capture_dispatch_outbox AS o
                  WHERE o.capture_id = c.id
              )
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_capture_dispatch_outbox_state_next_attempt_created",
        table_name="capture_dispatch_outbox",
    )
    op.drop_table("capture_dispatch_outbox")
