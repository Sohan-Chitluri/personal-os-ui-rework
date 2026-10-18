"""Versioned wire envelopes and validation helpers for sync protocol v1.

These models describe a future transport boundary only. They do not add an
HTTP route or imply that a production sync endpoint exists.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from personal_os.sync.contracts import (
    PROTOCOL_VERSION,
    Acknowledgement,
    AckStatus,
    ChangePage,
    ContractModel,
    EntitySnapshot,
    ErrorCategory,
    SyncCursor,
    SyncError,
    SyncOperation,
)

MAX_UPLOAD_BATCH_SIZE = 100
MAX_CHANGE_PAGE_SIZE = 500
SUPPORTED_PROTOCOL_VERSIONS = (PROTOCOL_VERSION,)
RETRYABLE_ERROR_CATEGORIES = frozenset(
    {
        ErrorCategory.TRANSPORT,
        ErrorCategory.TEMPORARY_SERVICE,
        ErrorCategory.STORAGE,
        ErrorCategory.MISSING_BASE,
        ErrorCategory.DEPENDENCY_PENDING,
    }
)


class VersionNegotiationRequest(ContractModel):
    """Versions a client can speak; the first supported overlap is selected."""

    client_id: str = Field(min_length=1, max_length=200)
    supported_versions: tuple[int, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def versions_are_positive_and_unique(self) -> VersionNegotiationRequest:
        if any(version < 1 for version in self.supported_versions):
            raise ValueError("supported protocol versions must be positive integers")
        if len(set(self.supported_versions)) != len(self.supported_versions):
            raise ValueError("supported protocol versions cannot contain duplicates")
        return self


class VersionNegotiationResponse(ContractModel):
    supported_versions: tuple[int, ...] = Field(min_length=1)
    selected_version: int | None = Field(default=None, ge=1)
    error: SyncError | None = None

    @model_validator(mode="after")
    def selection_matches_error(self) -> VersionNegotiationResponse:
        if not self.supported_versions or any(version < 1 for version in self.supported_versions):
            raise ValueError("server supported versions must be positive integers")
        if len(set(self.supported_versions)) != len(self.supported_versions):
            raise ValueError("server supported versions cannot contain duplicates")
        if (self.selected_version is None) == (self.error is None):
            raise ValueError("response must contain either a selected version or an error")
        if (
            self.selected_version is not None
            and self.selected_version not in self.supported_versions
        ):
            raise ValueError("selected version must be supported by the server")
        if self.error is not None and self.error.category != ErrorCategory.PROTOCOL_MISMATCH:
            raise ValueError("version negotiation errors must be protocol_mismatch")
        if self.error is not None:
            validate_sync_error(self.error)
        return self


class UploadBatch(ContractModel):
    """Ordered, bounded upload batch; envelope version remains an integer so
    unsupported versions can be rejected before decoding v1 operation bodies.
    """

    protocol_version: int = Field(ge=1)
    batch_id: UUID
    operations: tuple[SyncOperation, ...] = Field(min_length=1, max_length=MAX_UPLOAD_BATCH_SIZE)

    @model_validator(mode="after")
    def operation_ids_and_dependencies_are_well_formed(self) -> UploadBatch:
        operation_ids = [operation.operation_id for operation in self.operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("a batch cannot contain the same operation ID more than once")

        positions = {operation_id: index for index, operation_id in enumerate(operation_ids)}
        for index, operation in enumerate(self.operations):
            for dependency in operation.depends_on:
                dependency_position = positions.get(dependency)
                if dependency_position is not None and dependency_position >= index:
                    raise ValueError("in-batch dependencies must precede their dependents")
        return self


class UploadResponse(ContractModel):
    """Either a batch-level error or one ordered acknowledgement per operation."""

    protocol_version: int = Field(ge=1)
    batch_id: UUID
    acknowledgements: tuple[Acknowledgement, ...] = ()
    error: SyncError | None = None

    @model_validator(mode="after")
    def response_has_one_outcome(self) -> UploadResponse:
        has_acks = bool(self.acknowledgements)
        if has_acks == (self.error is not None):
            raise ValueError("response must contain either acknowledgements or a batch error")
        ids = [ack.operation_id for ack in self.acknowledgements]
        if len(ids) != len(set(ids)):
            raise ValueError("response cannot acknowledge an operation more than once")
        if self.error is not None:
            validate_sync_error(self.error)
        for ack in self.acknowledgements:
            if (
                ack.status in (AckStatus.APPLIED, AckStatus.DUPLICATE)
                and ack.error_category is not None
            ):
                raise ValueError("successful acknowledgement cannot contain an error category")
            if ack.status == AckStatus.RETRYABLE and (
                ack.error_category is None or not is_retryable_category(ack.error_category)
            ):
                raise ValueError("retryable acknowledgement requires a retryable error category")
            if ack.status == AckStatus.PERMANENT and (
                ack.error_category is None or is_retryable_category(ack.error_category)
            ):
                raise ValueError("permanent acknowledgement requires a permanent error category")
        return self


class ChangeFeedResponse(ContractModel):
    """A page and its expiring opaque cursor, or a batch-level feed error."""

    protocol_version: int = Field(ge=1)
    page: ChangePage | None = None
    cursor: SyncCursor | None = None
    error: SyncError | None = None

    @model_validator(mode="after")
    def page_cursor_and_error_are_consistent(self) -> ChangeFeedResponse:
        if self.error is not None:
            if self.page is not None or self.cursor is not None:
                raise ValueError("failed feed response cannot include a page or cursor")
            validate_sync_error(self.error)
            return self
        if self.page is None or self.cursor is None:
            raise ValueError("successful feed response requires both page and cursor")
        if len(self.page.items) > MAX_CHANGE_PAGE_SIZE:
            raise ValueError(f"change-feed pages cannot exceed {MAX_CHANGE_PAGE_SIZE} items")
        if self.page.next_cursor != self.cursor.value:
            raise ValueError("cursor value must match the page next_cursor")
        return self


class SnapshotResponse(ContractModel):
    """Atomic recovery snapshot and feed position for an expired cursor."""

    protocol_version: int = Field(ge=1)
    snapshots: tuple[EntitySnapshot, ...]
    cursor: SyncCursor

    @model_validator(mode="after")
    def snapshot_has_one_current_value_per_entity(self) -> SnapshotResponse:
        identities = [(snapshot.entity_type, snapshot.entity_id) for snapshot in self.snapshots]
        if len(identities) != len(set(identities)):
            raise ValueError("snapshot response cannot repeat an entity identity")
        return self


def negotiate_versions(
    request: VersionNegotiationRequest,
    *,
    supported_versions: tuple[int, ...] = SUPPORTED_PROTOCOL_VERSIONS,
) -> VersionNegotiationResponse:
    """Select the highest common version, or return an inspectable mismatch."""

    common = sorted(set(request.supported_versions).intersection(supported_versions), reverse=True)
    if common:
        return VersionNegotiationResponse(
            supported_versions=supported_versions,
            selected_version=common[0],
        )
    return VersionNegotiationResponse(
        supported_versions=supported_versions,
        error=SyncError(
            category=ErrorCategory.PROTOCOL_MISMATCH,
            retryable=False,
            message="client and server have no supported sync protocol version in common",
        ),
    )


def validate_upload_batch(
    batch: UploadBatch,
    *,
    supported_versions: tuple[int, ...] = SUPPORTED_PROTOCOL_VERSIONS,
) -> SyncError | None:
    """Return a batch-level permanent error before any operation is applied."""

    if batch.protocol_version not in supported_versions:
        return SyncError(
            category=ErrorCategory.PROTOCOL_MISMATCH,
            retryable=False,
            message=f"unsupported sync protocol version: {batch.protocol_version}",
        )
    if any(operation.protocol_version != batch.protocol_version for operation in batch.operations):
        return SyncError(
            category=ErrorCategory.PROTOCOL_MISMATCH,
            retryable=False,
            message="operation protocol versions must match the upload envelope",
        )
    return None


def validate_upload_response(
    batch: UploadBatch,
    response: UploadResponse,
    *,
    supported_versions: tuple[int, ...] = SUPPORTED_PROTOCOL_VERSIONS,
) -> None:
    """Raise if a response cannot safely be matched to the submitted batch."""

    if response.batch_id != batch.batch_id:
        raise ValueError("upload response batch_id does not match request")
    if response.protocol_version not in supported_versions:
        raise ValueError("upload response uses an unsupported protocol version")
    if response.error is not None:
        return
    expected = [operation.operation_id for operation in batch.operations]
    actual = [ack.operation_id for ack in response.acknowledgements]
    if actual != expected:
        raise ValueError("successful upload response must acknowledge every operation in order")
    if response.protocol_version != batch.protocol_version:
        raise ValueError("upload response protocol version does not match request")


def expired_cursor_error(cursor: SyncCursor, *, now: datetime) -> SyncError | None:
    """Return the explicit non-retryable expiry outcome at the cursor boundary."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone")
    if cursor.expires_at <= now:
        return SyncError(
            category=ErrorCategory.CURSOR_EXPIRED,
            retryable=False,
            message="change-feed cursor expired; apply a fresh snapshot before resuming",
        )
    return None


def is_retryable_category(category: ErrorCategory) -> bool:
    """Whether protocol v1 permits retrying an error without changing input."""

    return category in RETRYABLE_ERROR_CATEGORIES


def validate_sync_error(error: SyncError) -> None:
    """Enforce the v1 retryable/permanent classification for batch errors."""

    if error.retryable != is_retryable_category(error.category):
        classification = "retryable" if is_retryable_category(error.category) else "permanent"
        raise ValueError(f"{error.category.value} errors must be marked {classification}")


def protocol_mismatch_response(batch: UploadBatch) -> UploadResponse:
    """Build the batch-level mismatch shape without acknowledging queued work."""

    error = validate_upload_batch(batch)
    if error is None:
        raise ValueError("batch does not have a protocol mismatch")
    return UploadResponse(
        protocol_version=PROTOCOL_VERSION,
        batch_id=batch.batch_id,
        error=error,
    )


__all__ = [
    "MAX_CHANGE_PAGE_SIZE",
    "MAX_UPLOAD_BATCH_SIZE",
    "RETRYABLE_ERROR_CATEGORIES",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "ChangeFeedResponse",
    "SnapshotResponse",
    "UploadBatch",
    "UploadResponse",
    "VersionNegotiationRequest",
    "VersionNegotiationResponse",
    "expired_cursor_error",
    "is_retryable_category",
    "negotiate_versions",
    "protocol_mismatch_response",
    "validate_upload_batch",
    "validate_upload_response",
    "validate_sync_error",
]
