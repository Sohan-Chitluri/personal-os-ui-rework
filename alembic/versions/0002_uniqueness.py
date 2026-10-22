"""Uniqueness constraints for idempotency and one-job-per-capture.

Revision ID: 0002_uniqueness
Revises: 0001_initial
Create Date: 2026-10-19

Audit remediation: idempotency was application-level only. These DB constraints
make it impossible for concurrent requests/workers to create duplicates:
  * uq_captures_owner_idem           — one capture per (owner_id, idempotency_key)
  * uq_capture_processing_capture_id — exactly one processing row per capture
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_uniqueness"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One capture per (owner, client-supplied idempotency key). NULL keys are
    # exempt on both SQLite and PostgreSQL (multiple NULLs allowed).
    with op.batch_alter_table("captures") as batch:
        batch.create_unique_constraint(
            "uq_captures_owner_idem", ["owner_id", "idempotency_key"]
        )
    # Exactly one processing row per capture — concurrent recovery sweeps or
    # workers can never create duplicate jobs.
    with op.batch_alter_table("capture_processing") as batch:
        batch.create_unique_constraint(
            "uq_capture_processing_capture_id", ["capture_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("capture_processing") as batch:
        batch.drop_constraint("uq_capture_processing_capture_id", type_="unique")
    with op.batch_alter_table("captures") as batch:
        batch.drop_constraint("uq_captures_owner_idem", type_="unique")
