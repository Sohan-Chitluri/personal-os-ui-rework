"""Add summary_attempts and next_retry_at to conversations for fair sweep retries.

Revision ID: 0013_conv_summary_retries
Revises: 0012_github_app_oauth
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0013_conv_summary_retries"
down_revision = "0012_github_app_oauth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "summary_attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_conversations_summary_retry",
        "conversations",
        ["summary_status", "next_retry_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_summary_retry", table_name="conversations")
    op.drop_column("conversations", "next_retry_at")
    op.drop_column("conversations", "summary_attempts")
