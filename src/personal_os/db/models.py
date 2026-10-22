"""M1-A ORM models for the core operational records.

Scope (Checkpoint 1 vertical slice): User, Project, Task, Capture,
CaptureProcessing. Deliberately minimal — captures reference projects via a
nullable FK and the extraction pipeline produces tasks; no join tables, no
per-device tables, no workspace tables yet.

See docs/DATA_MODEL.md for the entity contract. IDs are ULID strings stored as
CHAR(26); mutable records carry ``revision`` for optimistic locking.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from personal_os.db.base import (
    Base,
    RevisionMixin,
    SoftDeleteMixin,
    TimestampMixin,
    TZDateTime,
    ULIDPrimaryKeyMixin,
    ULIDType,
    utcnow,
)

JSONColumn = JSON


# ---- Users -----------------------------------------------------------------


class User(ULIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    # Cognito `sub` — stable external identity. Unique per user.
    cognito_sub: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    __table_args__ = (
        UniqueConstraint("cognito_sub", name="uq_users_cognito_sub"),
        UniqueConstraint("email", name="uq_users_email"),
    )


# ---- Projects --------------------------------------------------------------


class Project(ULIDPrimaryKeyMixin, TimestampMixin, RevisionMixin, SoftDeleteMixin, Base):
    __tablename__ = "projects"

    owner_id: Mapped[str] = mapped_column(
        ULIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default="active", server_default="active", nullable=False
    )
    markdown_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)

    __table_args__ = (
        Index("ix_projects_owner", "owner_id"),
        Index("ix_projects_owner_status", "owner_id", "status"),
    )


# ---- Tasks -----------------------------------------------------------------


class Task(ULIDPrimaryKeyMixin, TimestampMixin, RevisionMixin, SoftDeleteMixin, Base):
    __tablename__ = "tasks"

    owner_id: Mapped[str] = mapped_column(
        ULIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[str | None] = mapped_column(
        ULIDType, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    capture_id: Mapped[str | None] = mapped_column(
        ULIDType, ForeignKey("captures.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default="open", server_default="open", nullable=False
    )
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    due_date: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    hard_deadline: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    extraction_meta: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)

    __table_args__ = (
        Index("ix_tasks_owner", "owner_id"),
        Index("ix_tasks_owner_status", "owner_id", "status"),
        Index("ix_tasks_project", "project_id"),
        Index("ix_tasks_capture", "capture_id"),
        Index(
            "uq_tasks_owner_client_message_id",
            "owner_id",
            text("(extraction_meta->>'client_message_id')"),
            unique=True,
            postgresql_where=text(
                "extraction_meta IS NOT NULL AND extraction_meta->>'client_message_id' IS NOT NULL"
            ),
            sqlite_where=text(
                "extraction_meta IS NOT NULL "
                "AND json_extract(extraction_meta, '$.client_message_id') IS NOT NULL"
            ),
        ),
    )


# ---- Captures --------------------------------------------------------------


class Capture(ULIDPrimaryKeyMixin, TimestampMixin, RevisionMixin, SoftDeleteMixin, Base):
    __tablename__ = "captures"

    owner_id: Mapped[str] = mapped_column(
        ULIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[str | None] = mapped_column(
        ULIDType, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    # Raw captured content, exactly as submitted (text or transcript).
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(32), default="web", server_default="web", nullable=False
    )
    content_type: Mapped[str] = mapped_column(
        String(32), default="text", server_default="text", nullable=False
    )
    # Client-supplied idempotency key — unique per owner (DB-enforced).
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default="pending", server_default="pending", nullable=False
    )
    metadata_json: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)

    __table_args__ = (
        Index("ix_captures_owner", "owner_id"),
        Index("ix_captures_owner_status", "owner_id", "status"),
        Index("ix_captures_owner_idem", "owner_id", "idempotency_key"),
        # DB-enforced idempotency: one capture per (owner, client key). NULL
        # keys are exempt (both SQLite and PostgreSQL allow multiple NULLs).
        UniqueConstraint("owner_id", "idempotency_key", name="uq_captures_owner_idem"),
    )


# ---- CaptureProcessing (per-capture pipeline result) -----------------------


class CaptureProcessing(ULIDPrimaryKeyMixin, TimestampMixin, RevisionMixin, Base):
    __tablename__ = "capture_processing"

    capture_id: Mapped[str] = mapped_column(
        ULIDType, ForeignKey("captures.id", ondelete="CASCADE"), nullable=False
    )
    # pending | processing | succeeded | failed
    status: Mapped[str] = mapped_column(
        String(32), default="pending", server_default="pending", nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    max_attempts: Mapped[int] = mapped_column(
        Integer, default=3, server_default="3", nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)
    # Deduplicated task IDs produced by the extraction pipeline.
    task_ids: Mapped[list | None] = mapped_column(JSONColumn(), nullable=True)

    __table_args__ = (
        Index("ix_capture_processing_capture", "capture_id"),
        Index("ix_capture_processing_status", "status"),
        # Exactly one processing row per capture, enforced by the DB — two
        # concurrent workers/recoveries can never create a duplicate job.
        UniqueConstraint("capture_id", name="uq_capture_processing_capture_id"),
    )


class CaptureDispatchOutbox(ULIDPrimaryKeyMixin, Base):
    """Transactional dispatch record for a persisted capture.

    The capture row remains the source of truth. This table only guarantees
    that dispatch intent is committed atomically with capture persistence.
    """

    __tablename__ = "capture_dispatch_outbox"

    capture_id: Mapped[str] = mapped_column(
        ULIDType, ForeignKey("captures.id", ondelete="CASCADE"), nullable=False
    )
    # pending | dispatched
    state: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, server_default=func.now(), nullable=False
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("capture_id", name="uq_capture_dispatch_outbox_capture_id"),
        Index(
            "ix_capture_dispatch_outbox_state_next_attempt_created",
            "state",
            "next_attempt_at",
            "created_at",
        ),
    )


# ---- Devices (Local Companion Registry) -----------------------------------


class Device(ULIDPrimaryKeyMixin, TimestampMixin, RevisionMixin, Base):
    __tablename__ = "devices"

    owner_id: Mapped[str] = mapped_column(
        ULIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    platform: Mapped[str] = mapped_column(
        String(32), default="linux", server_default="linux", nullable=False
    )
    capabilities: Mapped[dict] = mapped_column(JSONColumn(), default=dict, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="offline", server_default="offline", nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    registered_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    public_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def user_id(self) -> str:
        return self.owner_id

    __table_args__ = (
        UniqueConstraint("device_id", name="uq_devices_device_id"),
        Index("ix_devices_owner", "owner_id"),
        Index("ix_devices_owner_status", "owner_id", "status"),
        Index("ix_devices_device_id", "device_id"),
    )


# ---- Device Operations (Capability Execution Lifecycle) -------------------


class DeviceOperation(ULIDPrimaryKeyMixin, TimestampMixin, RevisionMixin, Base):
    __tablename__ = "device_operations"

    owner_id: Mapped[str] = mapped_column(
        ULIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False
    )
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict] = mapped_column(JSONColumn(), default=dict, nullable=False)
    # pending | running | completed | failed | timeout | cancelled
    status: Mapped[str] = mapped_column(
        String(32), default="pending", server_default="pending", nullable=False
    )
    result: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)
    error: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(
        Integer, default=15, server_default="15", nullable=False
    )

    __table_args__ = (
        Index("ix_device_operations_owner", "owner_id"),
        Index("ix_device_operations_device", "device_id"),
        Index("ix_device_operations_status", "status"),
        Index("ix_device_operations_owner_idem", "owner_id", "device_id", "idempotency_key"),
        UniqueConstraint(
            "owner_id", "device_id", "idempotency_key", name="uq_device_operations_idempotency"
        ),
    )


# ---- Conversations & Messages (Raphael MVP) -------------------------------


class Conversation(ULIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    owner_id: Mapped[str] = mapped_column(
        ULIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(
        String(255),
        default="Conversation with Raphael",
        server_default="Conversation with Raphael",
        nullable=False,
    )
    hermes_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default="active", server_default="active", nullable=False
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, server_default=func.now(), nullable=False
    )
    summary_status: Mapped[str] = mapped_column(
        String(32), default="active", server_default="active", nullable=False
    )
    summarized_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    summary_capture_id: Mapped[str | None] = mapped_column(
        ULIDType, ForeignKey("captures.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        Index("ix_conversations_owner", "owner_id"),
        Index("ix_conversations_owner_created", "owner_id", "created_at"),
        Index("ix_conversations_summary_status", "summary_status", "last_activity_at"),
    )


class ConversationMessage(ULIDPrimaryKeyMixin, Base):
    __tablename__ = "conversation_messages"

    conversation_id: Mapped[str] = mapped_column(
        ULIDType, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    client_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_conv_messages_conversation", "conversation_id"),
        Index("ix_conv_messages_created", "conversation_id", "created_at"),
        UniqueConstraint("conversation_id", "client_message_id", name="uq_conv_messages_client_id"),
    )


# ---- GitHub Integration (Auth & Repository Association) ------------------


class UserGitHubAuth(ULIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_github_auth"

    user_id: Mapped[str] = mapped_column(
        ULIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    encrypted_token: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    github_username: Mapped[str] = mapped_column(String(255), nullable=False)
    scopes: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_github_auth_user_id"),
        Index("ix_user_github_auth_user", "user_id"),
    )


class GitHubOAuthState(ULIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "github_oauth_states"

    user_id: Mapped[str] = mapped_column(
        ULIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    return_to: Mapped[str | None] = mapped_column(String(512), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    used: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )

    __table_args__ = (
        Index("ix_github_oauth_states_state", "state"),
        Index("ix_github_oauth_states_user", "user_id"),
    )


class ProjectRepository(ULIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "project_repositories"

    project_id: Mapped[str] = mapped_column(
        ULIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[str] = mapped_column(
        ULIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    repo_id: Mapped[str] = mapped_column(String(64), nullable=False)
    repo_owner: Mapped[str] = mapped_column(String(255), nullable=False)
    repo_name: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(512), nullable=False)
    default_branch: Mapped[str] = mapped_column(
        String(128), default="main", server_default="main", nullable=False
    )
    html_url: Mapped[str] = mapped_column(String(512), nullable=False)
    project_brief: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)
    readme_sha: Mapped[str | None] = mapped_column(String(128), nullable=True)
    readme_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", name="uq_project_repositories_project_id"),
        Index("ix_project_repos_owner", "owner_id"),
        Index("ix_project_repos_full_name", "full_name"),
    )


__all__ = [
    "User",
    "Project",
    "Task",
    "Capture",
    "CaptureProcessing",
    "CaptureDispatchOutbox",
    "Device",
    "DeviceOperation",
    "Conversation",
    "ConversationMessage",
    "UserGitHubAuth",
    "GitHubOAuthState",
    "ProjectRepository",
    "JSONColumn",
]
