"""Add device_operations table for capability execution lifecycle.

Revision ID: 0005_device_operations
Revises: 0004_devices
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_device_operations"
down_revision = "0004_devices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_operations",
        sa.Column("id", sa.CHAR(length=26), primary_key=True, nullable=False),
        sa.Column(
            "owner_id",
            sa.CHAR(length=26),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.String(length=128),
            sa.ForeignKey("devices.device_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "timeout_seconds",
            sa.Integer(),
            server_default="15",
            nullable=False,
        ),
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
        sa.UniqueConstraint(
            "owner_id",
            "device_id",
            "idempotency_key",
            name="uq_device_operations_idempotency",
        ),
    )
    op.create_index("ix_device_operations_owner", "device_operations", ["owner_id"])
    op.create_index("ix_device_operations_device", "device_operations", ["device_id"])
    op.create_index("ix_device_operations_status", "device_operations", ["status"])
    op.create_index(
        "ix_device_operations_owner_idem",
        "device_operations",
        ["owner_id", "device_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_device_operations_owner_idem", table_name="device_operations")
    op.drop_index("ix_device_operations_status", table_name="device_operations")
    op.drop_index("ix_device_operations_device", table_name="device_operations")
    op.drop_index("ix_device_operations_owner", table_name="device_operations")
    op.drop_table("device_operations")
