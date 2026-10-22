"""Merge independent M2 device-operation and M3 sync-state migrations.

Revision ID: 0006_merge_sync_and_device_state
Revises: 0005_device_operations, 0005_sync_state
"""
from __future__ import annotations

revision = "0006_merge_sync_and_device_state"
down_revision = ("0005_device_operations", "0005_sync_state")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join the M2 and M3 additive migration branches."""


def downgrade() -> None:
    """Keep both prerequisite schemas intact when reverting the merge node."""
