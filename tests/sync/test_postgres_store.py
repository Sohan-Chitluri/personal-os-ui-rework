"""PostgreSQL integration tests for durable M3 synchronization state.

These tests use the PostgreSQL-only ``db_engine`` fixture. They deliberately
do not fall back to SQLite when the required local PostgreSQL database is not
available.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from personal_os.db.base import new_ulid
from personal_os.db.models import Task
from personal_os.sync.contracts import (
    AckStatus,
    ConflictStatus,
    EntityType,
    ErrorCategory,
    OperationType,
    SyncOperation,
)
from personal_os.sync.postgres_store import (
    PostgresSyncStore,
    SyncChangeEvent,
    SyncEntityRevision,
    SyncOperationReceipt,
    SyncTombstone,
)


def operation(
    *,
    entity_id: str,
    operation_type: OperationType,
    base_revision: int | None,
    title: str | None = None,
    operation_id=None,
) -> SyncOperation:
    return SyncOperation(
        operation_id=operation_id or uuid4(),
        idempotency_key=f"key-{uuid4()}",
        entity_type=EntityType.TASK,
        entity_id=entity_id,
        operation_type=operation_type,
        base_revision=base_revision,
        payload=(
            {
                "title": title,
                "description": None,
                "status": "open",
                "priority": None,
                "due_date": None,
                "project_id": None,
                "capture_id": None,
            }
            if operation_type != OperationType.DELETE
            else None
        ),
        created_at="2026-10-19T12:00:00Z",
    )


async def count_rows(session_factory, model, **filters) -> int:
    async with session_factory() as session:
        statement = select(func.count()).select_from(model)
        if filters:
            statement = statement.filter_by(**filters)
        return await session.scalar(statement) or 0


def test_store_refuses_non_postgresql_session_factories() -> None:
    fake_bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    fake_factory = SimpleNamespace(kw={"bind": fake_bind})
    with pytest.raises(ValueError, match="requires a PostgreSQL session factory"):
        PostgresSyncStore(fake_factory)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_duplicate_upload_replays_ack_and_commits_once(
    db_session_factory, test_user
) -> None:
    store = PostgresSyncStore(db_session_factory)
    task_id = new_ulid()
    create = operation(
        entity_id=task_id,
        operation_type=OperationType.CREATE,
        base_revision=None,
        title="created once",
    )

    first = await store.apply_operation(test_user.id, create)
    retry = await store.apply_operation(test_user.id, create)

    assert first.status == retry.status == AckStatus.APPLIED
    assert first.revision == retry.revision == 1
    assert await count_rows(
        db_session_factory,
        SyncOperationReceipt,
        owner_id=test_user.id,
        operation_id=create.operation_id,
    ) == 1
    assert await count_rows(
        db_session_factory,
        SyncChangeEvent,
        owner_id=test_user.id,
        entity_id=task_id,
    ) == 1
    async with db_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task is not None and task.title == "created once" and task.revision == 1


@pytest.mark.asyncio
async def test_reused_operation_identity_with_changed_payload_is_rejected(
    db_session_factory, test_user
) -> None:
    store = PostgresSyncStore(db_session_factory)
    task_id = new_ulid()
    original = operation(
        entity_id=task_id,
        operation_type=OperationType.CREATE,
        base_revision=None,
        title="original",
    )
    await store.apply_operation(test_user.id, original)
    changed = original.model_copy(
        update={
            "payload": {
                **original.payload,
                "title": "changed request body",
            }
        }
    )

    result = await store.apply_operation(test_user.id, changed)

    assert result.status == AckStatus.PERMANENT
    assert result.error_category == ErrorCategory.IDEMPOTENCY_MISMATCH
    assert await count_rows(
        db_session_factory,
        SyncOperationReceipt,
        owner_id=test_user.id,
        operation_id=original.operation_id,
    ) == 1
    async with db_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task is not None and task.title == "original" and task.revision == 1


@pytest.mark.asyncio
async def test_stale_edit_persists_both_sides_without_overwriting_current(
    db_session_factory, test_user
) -> None:
    store = PostgresSyncStore(db_session_factory)
    task_id = new_ulid()
    await store.apply_operation(
        test_user.id,
        operation(
            entity_id=task_id,
            operation_type=OperationType.CREATE,
            base_revision=None,
            title="base",
        ),
    )
    await store.apply_operation(
        test_user.id,
        operation(
            entity_id=task_id,
            operation_type=OperationType.UPDATE,
            base_revision=1,
            title="cloud edit",
        ),
    )
    local = operation(
        entity_id=task_id,
        operation_type=OperationType.UPDATE,
        base_revision=1,
        title="offline edit",
    )

    result = await store.apply_operation(test_user.id, local)

    assert result.status == AckStatus.CONFLICTED
    assert result.conflict_id is not None
    conflict = await store.get_conflict(test_user.id, result.conflict_id)
    assert conflict is not None and conflict.status == ConflictStatus.UNRESOLVED
    assert conflict.base_snapshot is not None and conflict.base_snapshot.revision == 1
    assert conflict.local_payload == local.payload
    assert conflict.remote_snapshot.revision == 2
    assert conflict.remote_snapshot.data["title"] == "cloud edit"
    async with db_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task is not None and task.title == "cloud edit" and task.revision == 2
    assert await count_rows(
        db_session_factory,
        SyncEntityRevision,
        owner_id=test_user.id,
        entity_id=task_id,
        revision=1,
    ) == 1


@pytest.mark.asyncio
async def test_delete_persists_tombstone_and_ordered_change_feed(
    db_session_factory, test_user
) -> None:
    store = PostgresSyncStore(db_session_factory)
    task_id = new_ulid()
    await store.apply_operation(
        test_user.id,
        operation(
            entity_id=task_id,
            operation_type=OperationType.CREATE,
            base_revision=None,
            title="delete me",
        ),
    )
    deletion = await store.apply_operation(
        test_user.id,
        operation(
            entity_id=task_id,
            operation_type=OperationType.DELETE,
            base_revision=1,
        ),
    )

    assert deletion.status == AckStatus.APPLIED and deletion.revision == 2
    page = await store.list_changes(test_user.id, after_sequence=0, limit=10)
    assert [item.sequence for item in page] == sorted(item.sequence for item in page)
    tombstone_item = page[-1]
    assert tombstone_item.tombstone is not None
    assert tombstone_item.tombstone.revision == deletion.revision
    assert await count_rows(
        db_session_factory,
        SyncTombstone,
        owner_id=test_user.id,
        entity_id=task_id,
        revision=2,
    ) == 1
    async with db_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task is not None and task.deleted_at is not None and task.revision == 2


@pytest.mark.asyncio
async def test_database_failure_rolls_back_entity_receipt_revision_and_feed(
    db_session_factory, test_user, monkeypatch
) -> None:
    store = PostgresSyncStore(db_session_factory)
    task_id = new_ulid()
    create = operation(
        entity_id=task_id,
        operation_type=OperationType.CREATE,
        base_revision=None,
        title="must roll back",
    )

    async def fail_feed(*args, **kwargs):
        raise RuntimeError("simulated feed write failure")

    monkeypatch.setattr(store, "_append_change", fail_feed)
    with pytest.raises(RuntimeError, match="simulated feed write failure"):
        await store.apply_operation(test_user.id, create)

    async with db_session_factory() as session:
        assert await session.get(Task, task_id) is None
    assert await count_rows(
        db_session_factory, SyncOperationReceipt, owner_id=test_user.id
    ) == 0
    assert await count_rows(
        db_session_factory, SyncEntityRevision, owner_id=test_user.id
    ) == 0
    assert await count_rows(
        db_session_factory, SyncChangeEvent, owner_id=test_user.id
    ) == 0


@pytest.mark.asyncio
async def test_concurrent_duplicate_delivery_commits_one_operation(
    db_session_factory, test_user
) -> None:
    store = PostgresSyncStore(db_session_factory)
    create = operation(
        entity_id=new_ulid(),
        operation_type=OperationType.CREATE,
        base_revision=None,
        title="concurrent once",
    )

    first, second = await asyncio.gather(
        store.apply_operation(test_user.id, create),
        store.apply_operation(test_user.id, create),
    )

    assert first == second
    assert first.status == AckStatus.APPLIED
    assert await count_rows(
        db_session_factory,
        SyncOperationReceipt,
        owner_id=test_user.id,
        operation_id=create.operation_id,
    ) == 1


@pytest.mark.asyncio
async def test_reordered_dependent_operation_is_retryable_until_receipt_exists(
    db_session_factory, test_user
) -> None:
    store = PostgresSyncStore(db_session_factory)
    prerequisite = operation(
        entity_id=new_ulid(),
        operation_type=OperationType.CREATE,
        base_revision=None,
        title="prerequisite",
    )
    dependent = operation(
        entity_id=new_ulid(),
        operation_type=OperationType.CREATE,
        base_revision=None,
        title="dependent",
    ).model_copy(update={"depends_on": (prerequisite.operation_id,)})

    early = await store.apply_operation(test_user.id, dependent)
    assert early.status == AckStatus.RETRYABLE
    assert early.error_category == ErrorCategory.DEPENDENCY_PENDING
    assert await count_rows(
        db_session_factory,
        SyncOperationReceipt,
        owner_id=test_user.id,
        operation_id=dependent.operation_id,
    ) == 0

    prerequisite_ack = await store.apply_operation(test_user.id, prerequisite)
    dependent_ack = await store.apply_operation(test_user.id, dependent)
    assert prerequisite_ack.status == dependent_ack.status == AckStatus.APPLIED
