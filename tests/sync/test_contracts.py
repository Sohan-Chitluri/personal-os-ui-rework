from datetime import UTC, datetime
from uuid import uuid4

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
    SyncOperation,
    Tombstone,
)

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)


def operation(**overrides: object) -> SyncOperation:
    fields: dict[str, object] = {
        "operation_id": uuid4(),
        "idempotency_key": "stable-key-1",
        "entity_type": EntityType.TASK,
        "entity_id": "task-1",
        "operation_type": OperationType.UPDATE,
        "base_revision": 2,
        "payload": {"title": "Local title"},
        "created_at": NOW,
    }
    fields.update(overrides)
    return SyncOperation.model_validate(fields)


def test_operation_requires_exact_base_for_mutations_and_create_has_no_base() -> None:
    with pytest.raises(ValidationError, match="update requires base_revision"):
        operation(base_revision=None)

    created = operation(
        operation_type=OperationType.CREATE,
        base_revision=None,
        payload={"title": "New task"},
    )
    assert created.base_revision is None

    with pytest.raises(ValidationError, match="create requires null base_revision"):
        operation(operation_type=OperationType.CREATE, base_revision=0)


def test_timestamps_are_aware_and_normalized_to_utc() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        operation(created_at=datetime(2026, 10, 19, 12))

    shifted = operation(created_at="2026-10-19T17:30:00+05:30")
    assert shifted.created_at == NOW


def test_acknowledgements_require_conflict_and_error_details() -> None:
    operation_id = uuid4()
    with pytest.raises(ValidationError, match="requires conflict_id"):
        Acknowledgement(operation_id=operation_id, status=AckStatus.CONFLICTED)
    with pytest.raises(ValidationError, match="requires error_category"):
        Acknowledgement(operation_id=operation_id, status=AckStatus.RETRYABLE)

    permanent = Acknowledgement(
        operation_id=operation_id,
        status=AckStatus.PERMANENT,
        error_category=ErrorCategory.UNSUPPORTED,
    )
    assert permanent.error_category == ErrorCategory.UNSUPPORTED


def test_change_item_must_match_snapshot_identity_and_revision() -> None:
    snapshot = EntitySnapshot(
        entity_type=EntityType.TASK,
        entity_id="task-1",
        revision=3,
        data={"title": "Remote title"},
    )
    with pytest.raises(ValidationError, match="identity and revision"):
        ChangeFeedItem(
            event_id=uuid4(),
            sequence=7,
            entity_type=EntityType.TASK,
            entity_id="task-1",
            revision=4,
            snapshot=snapshot,
        )


def test_tombstone_is_revisioned_and_sequence_matches_change_item() -> None:
    tombstone = Tombstone(
        entity_type=EntityType.PROJECT,
        entity_id="project-1",
        revision=5,
        sequence=9,
        deleted_at=NOW,
    )
    item = ChangeFeedItem(
        event_id=uuid4(),
        sequence=9,
        entity_type=EntityType.PROJECT,
        entity_id="project-1",
        revision=5,
        tombstone=tombstone,
    )
    assert item.tombstone == tombstone

    with pytest.raises(ValidationError, match="sequence must match"):
        ChangeFeedItem(
            event_id=uuid4(),
            sequence=10,
            entity_type=EntityType.PROJECT,
            entity_id="project-1",
            revision=5,
            tombstone=tombstone,
        )


def test_pages_reject_reordered_or_duplicate_sequence_numbers() -> None:
    def item(sequence: int) -> ChangeFeedItem:
        snapshot = EntitySnapshot(
            entity_type=EntityType.TASK,
            entity_id=f"task-{sequence}",
            revision=sequence,
            data={"title": "task"},
        )
        return ChangeFeedItem(
            event_id=uuid4(),
            sequence=sequence,
            entity_type=EntityType.TASK,
            entity_id=f"task-{sequence}",
            revision=sequence,
            snapshot=snapshot,
        )

    page = ChangePage(items=(item(2), item(4)), next_cursor="cursor-b", has_more=True)
    assert [change.sequence for change in page.items] == [2, 4]

    with pytest.raises(ValidationError, match="strictly increasing"):
        ChangePage(items=(item(4), item(2)), next_cursor="cursor-b", has_more=True)
    with pytest.raises(ValidationError, match="strictly increasing"):
        ChangePage(items=(item(2), item(2)), next_cursor="cursor-b", has_more=False)
