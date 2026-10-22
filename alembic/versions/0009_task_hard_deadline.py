"""Add hard_deadline column to tasks.

Revision ID: 0009_task_hard_deadline
Revises: 0008_raphael_conversations
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_task_hard_deadline"
down_revision = "0008_raphael_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("hard_deadline", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "hard_deadline")
