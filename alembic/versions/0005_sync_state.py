"""Add PostgreSQL-backed sync receipts, revisions, conflicts, and feed.

Revision ID: 0005_sync_state
Revises: 0004_devices
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_sync_state"
down_revision = "0004_devices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_operation_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.CHAR(26), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("operation_envelope", sa.JSON(), nullable=False),
        sa.Column("acknowledgement", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_sync_receipts_owner_operation",
        "sync_operation_receipts",
        ["owner_id", "operation_id"],
        unique=True,
    )
    op.create_index(
        "uq_sync_receipts_owner_idempotency",
        "sync_operation_receipts",
        ["owner_id", "idempotency_key"],
        unique=True,
    )

    op.create_table(
        "sync_entity_revisions",
        sa.Column("owner_id", sa.CHAR(26), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=200), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("owner_id", "entity_type", "entity_id", "revision"),
    )

    op.create_table(
        "sync_conflicts",
        sa.Column("conflict_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.CHAR(26), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("record", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolution_operation_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("conflict_id"),
    )
    op.create_index(
        "ix_sync_conflicts_owner_status",
        "sync_conflicts",
        ["owner_id", "status"],
    )
    op.create_index(
        "ix_sync_conflicts_owner_entity",
        "sync_conflicts",
        ["owner_id", "entity_type", "entity_id"],
    )

    op.create_table(
        "sync_change_events",
        sa.Column("sequence", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.CHAR(26), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=200), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column("tombstone", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index(
        "ix_sync_change_events_owner_sequence",
        "sync_change_events",
        ["owner_id", "sequence"],
    )
    op.create_index(
        "uq_sync_change_events_owner_entity_revision",
        "sync_change_events",
        ["owner_id", "entity_type", "entity_id", "revision"],
        unique=True,
    )

    op.create_table(
        "sync_tombstones",
        sa.Column("owner_id", sa.CHAR(26), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=200), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["sync_change_events.event_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sequence"], ["sync_change_events.sequence"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("owner_id", "entity_type", "entity_id"),
    )


def downgrade() -> None:
    op.drop_table("sync_tombstones")
    op.drop_index(
        "uq_sync_change_events_owner_entity_revision",
        table_name="sync_change_events",
    )
    op.drop_index("ix_sync_change_events_owner_sequence", table_name="sync_change_events")
    op.drop_table("sync_change_events")
    op.drop_index("ix_sync_conflicts_owner_entity", table_name="sync_conflicts")
    op.drop_index("ix_sync_conflicts_owner_status", table_name="sync_conflicts")
    op.drop_table("sync_conflicts")
    op.drop_table("sync_entity_revisions")
    op.drop_index("uq_sync_receipts_owner_idempotency", table_name="sync_operation_receipts")
    op.drop_index("uq_sync_receipts_owner_operation", table_name="sync_operation_receipts")
    op.drop_table("sync_operation_receipts")
