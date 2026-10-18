"""Schema and boundary tests for the versioned M3 sync protocol."""

from datetime import timedelta
from uuid import UUID, uuid5

import pytest
from pydantic import ValidationError

from personal_os.sync.contracts import (
    Acknowledgement,
    AckStatus,
    ChangeFeedItem,
    ChangePage,
    EntitySnapshot,
    EntityType,
    ErrorCategory,
    OperationType,
    SyncCursor,
    SyncError,
    Tombstone,
)
from personal_os.sync.protocol import (
    MAX_CHANGE_PAGE_SIZE,
    MAX_UPLOAD_BATCH_SIZE,
    ChangeFeedResponse,
    UploadBatch,
    UploadResponse,
    VersionNegotiationRequest,
    expired_cursor_error,
    negotiate_versions,
    validate_upload_batch,
    validate_upload_response,
)
from personal_os.sync.simulator import SIMULATION_START, make_operation

_NAMESPACE = UUID("b003fe60-4736-45db-95c5-d33f0092ef12")


def uid(name: str) -> UUID:
    return uuid5(_NAMESPACE, name)


def operation(name: str, *, depends_on: tuple[UUID, ...] = ()):
    return make_operation(
        operation_id=uid(name),
        entity_id=f"entity-{name}",
        operation_type=OperationType.CREATE,
        base_revision=None,
        payload={"title": name},
        depends_on=depends_on,
    )


def test_version_negotiation_selects_highest_shared_version_and_reports_mismatch():
    request = VersionNegotiationRequest(
        client_id="browser-1",
        supported_versions=(1, 2),
    )
    response = negotiate_versions(request, supported_versions=(1,))
    assert response.selected_version == 1
    assert response.error is None

    mismatch = negotiate_versions(
        VersionNegotiationRequest(client_id="browser-2", supported_versions=(2, 3)),
        supported_versions=(1,),
    )
    assert mismatch.selected_version is None
    assert mismatch.error is not None
    assert mismatch.error.category == ErrorCategory.PROTOCOL_MISMATCH
    assert mismatch.error.retryable is False


def test_negotiation_rejects_duplicate_or_nonpositive_versions():
    with pytest.raises(ValidationError):
        VersionNegotiationRequest(client_id="browser", supported_versions=(1, 1))
    with pytest.raises(ValidationError):
        VersionNegotiationRequest(client_id="browser", supported_versions=(0,))


def test_upload_batch_rejects_duplicate_ids_and_reordered_in_batch_dependencies():
    first = operation("first")
    dependent = make_operation(
        operation_id=uid("dependent"),
        entity_id="entity-dependent",
        operation_type=OperationType.CREATE,
        base_revision=None,
        payload={"title": "dependent"},
        depends_on=(first.operation_id,),
    )
    with pytest.raises(ValidationError, match="dependencies must precede"):
        UploadBatch(protocol_version=1, batch_id=uid("batch"), operations=(dependent, first))
    with pytest.raises(ValidationError, match="same operation ID"):
        UploadBatch(protocol_version=1, batch_id=uid("dup"), operations=(first, first))


def test_upload_batch_is_bounded_and_unknown_operation_types_are_rejected():
    operations = tuple(operation(f"bounded-{index}") for index in range(MAX_UPLOAD_BATCH_SIZE + 1))
    with pytest.raises(ValidationError, match="at most"):
        UploadBatch(protocol_version=1, batch_id=uid("too-large"), operations=operations)

    unknown = operation("unknown-type").model_dump(mode="json")
    unknown["operation_type"] = "guess_what_to_do"
    with pytest.raises(ValidationError, match="guess_what_to_do"):
        UploadBatch.model_validate(
            {"protocol_version": 1, "batch_id": str(uid("unknown-batch")), "operations": [unknown]}
        )


def test_batch_version_mismatch_is_permanent_and_does_not_decode_as_v1():
    op = operation("version")
    batch = UploadBatch(protocol_version=2, batch_id=uid("mismatch"), operations=(op,))
    error = validate_upload_batch(batch)
    assert error is not None
    assert error.category == ErrorCategory.PROTOCOL_MISMATCH
    assert error.retryable is False


def test_upload_response_correlates_ordered_per_item_acknowledgements():
    first = operation("ack-first")
    second = operation("ack-second")
    batch = UploadBatch(protocol_version=1, batch_id=uid("acks"), operations=(first, second))
    response = UploadResponse(
        protocol_version=1,
        batch_id=batch.batch_id,
        acknowledgements=(
            Acknowledgement(operation_id=first.operation_id, status=AckStatus.APPLIED, revision=1),
            Acknowledgement(
                operation_id=second.operation_id,
                status=AckStatus.RETRYABLE,
                error_category=ErrorCategory.TEMPORARY_SERVICE,
            ),
        ),
    )
    validate_upload_response(batch, response)
    assert [ack.status for ack in response.acknowledgements] == [
        AckStatus.APPLIED,
        AckStatus.RETRYABLE,
    ]

    out_of_order = response.model_copy(
        update={"acknowledgements": tuple(reversed(response.acknowledgements))}
    )
    with pytest.raises(ValueError, match="every operation in order"):
        validate_upload_response(batch, out_of_order)


def test_error_retryability_is_classified_and_mismatched_flags_are_rejected():
    with pytest.raises(ValidationError, match="must be marked retryable"):
        UploadResponse(
            protocol_version=1,
            batch_id=uid("wrong-classification"),
            error=SyncError(
                category=ErrorCategory.TEMPORARY_SERVICE,
                retryable=False,
                message="temporary",
            ),
        )
    with pytest.raises(ValidationError, match="retryable error category"):
        UploadResponse(
            protocol_version=1,
            batch_id=uid("wrong-ack"),
            acknowledgements=(
                Acknowledgement(
                    operation_id=uid("bad-ack"),
                    status=AckStatus.RETRYABLE,
                    error_category=ErrorCategory.DEPENDENCY_FAILED,
                ),
            ),
        )

    terminal_dependency = UploadResponse(
        protocol_version=1,
        batch_id=uid("terminal-dependency"),
        acknowledgements=(
            Acknowledgement(
                operation_id=uid("terminal-dependent-operation"),
                status=AckStatus.PERMANENT,
                error_category=ErrorCategory.DEPENDENCY_FAILED,
            ),
        ),
    )
    assert terminal_dependency.acknowledgements[0].error_category == ErrorCategory.DEPENDENCY_FAILED


def test_feed_page_requires_matching_opaque_cursor_and_cursor_expiry_is_explicit():
    cursor = SyncCursor(value="opaque-next", expires_at=SIMULATION_START + timedelta(days=1))
    item = ChangeFeedItem(
        event_id=uid("feed-event"),
        sequence=1,
        entity_type=EntityType.TASK,
        entity_id="task-1",
        revision=1,
        snapshot=EntitySnapshot(
            entity_type=EntityType.TASK,
            entity_id="task-1",
            revision=1,
            data={"title": "one"},
        ),
    )
    page = ChangePage(items=(item,), next_cursor=cursor.value, has_more=False)
    response = ChangeFeedResponse(protocol_version=1, page=page, cursor=cursor)
    assert response.page == page
    with pytest.raises(ValidationError, match="must match"):
        ChangeFeedResponse(
            protocol_version=1,
            page=page,
            cursor=SyncCursor(value="wrong", expires_at=cursor.expires_at),
        )

    assert expired_cursor_error(cursor, now=cursor.expires_at) is not None
    assert expired_cursor_error(cursor, now=SIMULATION_START) is None


def test_change_feed_response_rejects_pages_larger_than_protocol_bound():
    items = tuple(
        ChangeFeedItem(
            event_id=uid(f"oversized-{sequence}"),
            sequence=sequence,
            entity_type=EntityType.TASK,
            entity_id=f"task-{sequence}",
            revision=1,
            snapshot=EntitySnapshot(
                entity_type=EntityType.TASK,
                entity_id=f"task-{sequence}",
                revision=1,
                data={"title": "item"},
            ),
        )
        for sequence in range(1, MAX_CHANGE_PAGE_SIZE + 2)
    )
    cursor = SyncCursor(value="oversized", expires_at=SIMULATION_START + timedelta(days=1))
    page = ChangePage(items=items, next_cursor=cursor.value, has_more=False)
    with pytest.raises(ValidationError, match="cannot exceed"):
        ChangeFeedResponse(protocol_version=1, page=page, cursor=cursor)


def test_tombstone_change_item_requires_matching_entity_revision_and_sequence():
    tombstone = Tombstone(
        entity_type=EntityType.PROJECT,
        entity_id="project-7",
        revision=3,
        sequence=12,
        deleted_at=SIMULATION_START,
    )
    item = ChangeFeedItem(
        event_id=uid("tombstone"),
        sequence=12,
        entity_type=EntityType.PROJECT,
        entity_id="project-7",
        revision=3,
        tombstone=tombstone,
    )
    assert item.tombstone == tombstone
    with pytest.raises(ValidationError, match="sequence must match"):
        ChangeFeedItem(
            event_id=uid("bad-tombstone"),
            sequence=13,
            entity_type=EntityType.PROJECT,
            entity_id="project-7",
            revision=3,
            tombstone=tombstone,
        )
