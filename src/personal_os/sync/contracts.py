"""Shared, versioned operation and reconciliation data contracts.

These models are intentionally independent of HTTP, SQLAlchemy, and any
particular queue or storage adapter.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROTOCOL_VERSION = 1


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EntityType(StrEnum):
    TASK = "task"
    PROJECT = "project"
    PROJECT_DOCUMENT = "project_document"


class OperationType(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    RESOLVE_CONFLICT = "resolve_conflict"


class AckStatus(StrEnum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    CONFLICTED = "conflicted"
    RETRYABLE = "retryable"
    PERMANENT = "permanent"


class ErrorCategory(StrEnum):
    TRANSPORT = "transport"
    TEMPORARY_SERVICE = "temporary_service"
    STORAGE = "storage"
    MALFORMED = "malformed"
    UNSUPPORTED = "unsupported"
    UNAUTHORIZED = "unauthorized"
    IDEMPOTENCY_MISMATCH = "idempotency_mismatch"
    MISSING_ENTITY = "missing_entity"
    MISSING_BASE = "missing_base"
    EXPIRED_BASE = "expired_base"
    STALE_REVISION = "stale_revision"
    INVALID_OPERATION = "invalid_operation"
    DEPENDENCY_PENDING = "dependency_pending"
    DEPENDENCY_FAILED = "dependency_failed"
    PROTOCOL_MISMATCH = "protocol_mismatch"
    CURSOR_EXPIRED = "cursor_expired"


class ConflictStatus(StrEnum):
    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"


class SyncOperation(ContractModel):
    protocol_version: Literal[1] = 1
    operation_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=200)
    entity_type: EntityType
    entity_id: str = Field(min_length=1, max_length=200)
    operation_type: OperationType
    base_revision: int | None
    payload: dict[str, Any] | None = None
    created_at: datetime
    depends_on: tuple[UUID, ...] = ()
    resolves_conflict_id: UUID | None = None

    @field_validator("created_at")
    @classmethod
    def created_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value.astimezone(UTC)

    @field_validator("base_revision")
    @classmethod
    def base_revision_must_be_nonnegative(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("base_revision must be nonnegative")
        return value

    @model_validator(mode="after")
    def validate_operation_shape(self) -> SyncOperation:
        if self.operation_type == OperationType.CREATE:
            if self.base_revision is not None or self.payload is None:
                raise ValueError("create requires null base_revision and a payload")
        elif self.operation_type == OperationType.DELETE:
            if self.base_revision is None or self.payload is not None:
                raise ValueError("delete requires base_revision and no payload")
        elif self.operation_type == OperationType.UPDATE:
            if self.base_revision is None or self.payload is None:
                raise ValueError("update requires base_revision and a payload")
        else:
            if (
                self.base_revision is None
                or self.payload is None
                or self.resolves_conflict_id is None
            ):
                raise ValueError(
                    "conflict resolution requires base_revision, payload, and conflict ID"
                )
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("depends_on cannot contain duplicate operation IDs")
        if self.operation_id in self.depends_on:
            raise ValueError("operation cannot depend on itself")
        return self


class EntitySnapshot(ContractModel):
    entity_type: EntityType
    entity_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=0)
    data: dict[str, Any] | None
    deleted: bool = False

    @model_validator(mode="after")
    def deleted_snapshot_has_no_live_data(self) -> EntitySnapshot:
        if self.deleted and self.data is not None:
            raise ValueError("deleted snapshot cannot contain live data")
        if not self.deleted and self.data is None:
            raise ValueError("live snapshot requires data")
        return self


class ConflictRecord(ContractModel):
    conflict_id: UUID
    operation: SyncOperation
    base_revision: int | None
    base_snapshot: EntitySnapshot | None = None
    local_payload: dict[str, Any] | None
    remote_snapshot: EntitySnapshot
    reason: ErrorCategory
    status: ConflictStatus = ConflictStatus.UNRESOLVED
    created_at: datetime
    resolution_operation_id: UUID | None = None


class Acknowledgement(ContractModel):
    operation_id: UUID
    status: AckStatus
    revision: int | None = Field(default=None, ge=0)
    conflict_id: UUID | None = None
    error_category: ErrorCategory | None = None
    message: str | None = None
    retry_after_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_status_details(self) -> Acknowledgement:
        if self.status == AckStatus.CONFLICTED and self.conflict_id is None:
            raise ValueError("conflicted acknowledgement requires conflict_id")
        if self.status in (AckStatus.RETRYABLE, AckStatus.PERMANENT) and (
            self.error_category is None
        ):
            raise ValueError("failed acknowledgement requires error_category")
        return self


class Tombstone(ContractModel):
    entity_type: EntityType
    entity_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=0)
    sequence: int = Field(ge=0)
    deleted_at: datetime

    @field_validator("deleted_at")
    @classmethod
    def deleted_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deleted_at must include a timezone")
        return value.astimezone(UTC)


class ChangeFeedItem(ContractModel):
    event_id: UUID
    sequence: int = Field(ge=0)
    entity_type: EntityType
    entity_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=0)
    snapshot: EntitySnapshot | None = None
    tombstone: Tombstone | None = None

    @model_validator(mode="after")
    def exactly_one_change_value(self) -> ChangeFeedItem:
        if (self.snapshot is None) == (self.tombstone is None):
            raise ValueError("change item requires exactly one snapshot or tombstone")
        value = self.snapshot if self.snapshot is not None else self.tombstone
        assert value is not None
        if (
            value.entity_type != self.entity_type
            or value.entity_id != self.entity_id
            or value.revision != self.revision
        ):
            raise ValueError("change item identity and revision must match its value")
        if self.tombstone is not None and self.sequence != self.tombstone.sequence:
            raise ValueError("change item sequence must match tombstone sequence")
        return self


class ChangePage(ContractModel):
    items: tuple[ChangeFeedItem, ...]
    next_cursor: str = Field(min_length=1)
    has_more: bool

    @model_validator(mode="after")
    def items_are_strictly_ordered(self) -> ChangePage:
        sequences = [item.sequence for item in self.items]
        if sequences != sorted(set(sequences)):
            raise ValueError("change page items must have strictly increasing sequences")
        return self


class SyncCursor(ContractModel):
    """Opaque resume cursor; clients persist it but do not parse it."""

    value: str = Field(min_length=1)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def expires_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        return value.astimezone(UTC)


class SyncError(ContractModel):
    category: ErrorCategory
    retryable: bool
    message: str
    operation_id: UUID | None = None
    retry_after_seconds: float | None = Field(default=None, ge=0)
