"""Add Raphael conversations and messages persistence.

Revision ID: 0008_raphael_conversations
Revises: 0007_sync_feed_cursors
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_raphael_conversations"
down_revision = "0007_sync_feed_cursors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.CHAR(length=26), nullable=False),
        sa.Column("owner_id", sa.CHAR(length=26), nullable=False),
        sa.Column(
            "title",
            sa.String(length=255),
            nullable=False,
            server_default="Conversation with Raphael",
        ),
        sa.Column("hermes_session_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
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
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversations_owner", "conversations", ["owner_id"])
    op.create_index(
        "ix_conversations_owner_created", "conversations", ["owner_id", "created_at"]
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.CHAR(length=26), nullable=False),
        sa.Column("conversation_id", sa.CHAR(length=26), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("client_message_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id", "client_message_id", name="uq_conv_messages_client_id"
        ),
    )
    op.create_index(
        "ix_conv_messages_conversation", "conversation_messages", ["conversation_id"]
    )
    op.create_index(
        "ix_conv_messages_created", "conversation_messages", ["conversation_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_conv_messages_created", table_name="conversation_messages")
    op.drop_index("ix_conv_messages_conversation", table_name="conversation_messages")
    op.drop_table("conversation_messages")

    op.drop_index("ix_conversations_owner_created", table_name="conversations")
    op.drop_index("ix_conversations_owner", table_name="conversations")
    op.drop_table("conversations")
