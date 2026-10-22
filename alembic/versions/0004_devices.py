"""Add devices table for M2 local companion registry.

Revision ID: 0004_devices
Revises: 0003_capture_dispatch_outbox
Create Date: 2026-10-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_devices"
down_revision = "0003_capture_dispatch_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.CHAR(length=26), primary_key=True, nullable=False),
        sa.Column(
            "owner_id",
            sa.CHAR(length=26),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "platform",
            sa.String(length=32),
            server_default="linux",
            nullable=False,
        ),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="offline",
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("public_key", sa.Text(), nullable=True),
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
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.UniqueConstraint("device_id", name="uq_devices_device_id"),
    )
    op.create_index("ix_devices_owner", "devices", ["owner_id"])
    op.create_index("ix_devices_owner_status", "devices", ["owner_id", "status"])
    op.create_index("ix_devices_device_id", "devices", ["device_id"])


def downgrade() -> None:
    op.drop_index("ix_devices_device_id", table_name="devices")
    op.drop_index("ix_devices_owner_status", table_name="devices")
    op.drop_index("ix_devices_owner", table_name="devices")
    op.drop_table("devices")
