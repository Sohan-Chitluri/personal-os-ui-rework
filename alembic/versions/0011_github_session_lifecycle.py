"""Add GitHub authentication, project repositories, and conversation session lifecycle.

Revision ID: 0011_github_session_lifecycle
Revises: 0010_task_client_id_unique
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_github_session_lifecycle"
down_revision = "0010_task_client_id_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Alter conversations table: add session lifecycle columns
    op.add_column(
        "conversations",
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "summary_status",
            sa.String(32),
            server_default="active",
            nullable=False,
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "summarized_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "summary_capture_id",
            sa.CHAR(26),
            sa.ForeignKey("captures.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_conversations_summary_status",
        "conversations",
        ["summary_status", "last_activity_at"],
    )

    # 2. Create user_github_auth table
    op.create_table(
        "user_github_auth",
        sa.Column("id", sa.CHAR(26), primary_key=True),
        sa.Column(
            "user_id",
            sa.CHAR(26),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("encrypted_token", sa.Text(), nullable=False),
        sa.Column("github_username", sa.String(255), nullable=False),
        sa.Column("scopes", sa.String(255), nullable=True),
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
        sa.UniqueConstraint("user_id", name="uq_user_github_auth_user_id"),
    )
    op.create_index("ix_user_github_auth_user", "user_github_auth", ["user_id"])

    # 3. Create project_repositories table
    op.create_table(
        "project_repositories",
        sa.Column("id", sa.CHAR(26), primary_key=True),
        sa.Column(
            "project_id",
            sa.CHAR(26),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_id",
            sa.CHAR(26),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("repo_id", sa.String(64), nullable=False),
        sa.Column("repo_owner", sa.String(255), nullable=False),
        sa.Column("repo_name", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(512), nullable=False),
        sa.Column(
            "default_branch",
            sa.String(128),
            server_default="main",
            nullable=False,
        ),
        sa.Column("html_url", sa.String(512), nullable=False),
        sa.Column("project_brief", sa.JSON(), nullable=True),
        sa.Column("readme_sha", sa.String(128), nullable=True),
        sa.Column("readme_content", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("project_id", name="uq_project_repositories_project_id"),
    )
    op.create_index("ix_project_repos_owner", "project_repositories", ["owner_id"])
    op.create_index("ix_project_repos_full_name", "project_repositories", ["full_name"])


def downgrade() -> None:
    op.drop_index("ix_project_repos_full_name", table_name="project_repositories")
    op.drop_index("ix_project_repos_owner", table_name="project_repositories")
    op.drop_table("project_repositories")

    op.drop_index("ix_user_github_auth_user", table_name="user_github_auth")
    op.drop_table("user_github_auth")

    op.drop_index("ix_conversations_summary_status", table_name="conversations")
    op.drop_column("conversations", "summary_capture_id")
    op.drop_column("conversations", "summarized_at")
    op.drop_column("conversations", "summary_status")
    op.drop_column("conversations", "last_activity_at")
