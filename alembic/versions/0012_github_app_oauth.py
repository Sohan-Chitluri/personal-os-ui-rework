"""Add GitHub OAuth state table and refresh token support to user_github_auth.

Revision ID: 0012_github_app_oauth
Revises: 0011_github_session_lifecycle
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_github_app_oauth"
down_revision = "0011_github_session_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Alter user_github_auth table to add token expiration and refresh token
    op.add_column(
        "user_github_auth",
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
    )
    op.add_column(
        "user_github_auth",
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 2. Create github_oauth_states table for CSRF state validation
    op.create_table(
        "github_oauth_states",
        sa.Column("id", sa.CHAR(26), primary_key=True),
        sa.Column(
            "user_id",
            sa.CHAR(26),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.String(512), unique=True, nullable=False),
        sa.Column("return_to", sa.String(512), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), server_default=sa.false(), nullable=False),
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
    )
    op.create_index("ix_github_oauth_states_state", "github_oauth_states", ["state"])
    op.create_index("ix_github_oauth_states_user", "github_oauth_states", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_github_oauth_states_user", table_name="github_oauth_states")
    op.drop_index("ix_github_oauth_states_state", table_name="github_oauth_states")
    op.drop_table("github_oauth_states")

    op.drop_column("user_github_auth", "token_expires_at")
    op.drop_column("user_github_auth", "encrypted_refresh_token")
