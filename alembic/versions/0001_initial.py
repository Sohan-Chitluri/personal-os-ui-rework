"""Initial schema: users, projects, tasks, captures, capture_processing.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-19
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.CHAR(26), primary_key=True),
        sa.Column("cognito_sub", sa.String(64), nullable=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("cognito_sub", name="uq_users_cognito_sub"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.CHAR(26), primary_key=True),
        sa.Column("owner_id", sa.CHAR(26), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("markdown_path", sa.String(512), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_projects_owner", "projects", ["owner_id"])
    op.create_index("ix_projects_owner_status", "projects", ["owner_id", "status"])

    op.create_table(
        "captures",
        sa.Column("id", sa.CHAR(26), primary_key=True),
        sa.Column("owner_id", sa.CHAR(26), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.CHAR(26), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="web"),
        sa.Column("content_type", sa.String(32), nullable=False, server_default="text"),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_captures_owner", "captures", ["owner_id"])
    op.create_index("ix_captures_owner_status", "captures", ["owner_id", "status"])
    op.create_index("ix_captures_owner_idem", "captures", ["owner_id", "idempotency_key"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.CHAR(26), primary_key=True),
        sa.Column("owner_id", sa.CHAR(26), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.CHAR(26), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("capture_id", sa.CHAR(26), sa.ForeignKey("captures.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extraction_meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_tasks_owner", "tasks", ["owner_id"])
    op.create_index("ix_tasks_owner_status", "tasks", ["owner_id", "status"])
    op.create_index("ix_tasks_project", "tasks", ["project_id"])
    op.create_index("ix_tasks_capture", "tasks", ["capture_id"])

    op.create_table(
        "capture_processing",
        sa.Column("id", sa.CHAR(26), primary_key=True),
        sa.Column("capture_id", sa.CHAR(26), sa.ForeignKey("captures.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("task_ids", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_capture_processing_capture", "capture_processing", ["capture_id"])
    op.create_index("ix_capture_processing_status", "capture_processing", ["status"])


def downgrade() -> None:
    op.drop_index("ix_capture_processing_status", "capture_processing")
    op.drop_index("ix_capture_processing_capture", "capture_processing")
    op.drop_table("capture_processing")
    op.drop_index("ix_tasks_capture", "tasks")
    op.drop_index("ix_tasks_project", "tasks")
    op.drop_index("ix_tasks_owner_status", "tasks")
    op.drop_index("ix_tasks_owner", "tasks")
    op.drop_table("tasks")
    op.drop_index("ix_captures_owner_idem", "captures")
    op.drop_index("ix_captures_owner_status", "captures")
    op.drop_index("ix_captures_owner", "captures")
    op.drop_table("captures")
    op.drop_index("ix_projects_owner_status", "projects")
    op.drop_index("ix_projects_owner", "projects")
    op.drop_table("projects")
    op.drop_table("users")
