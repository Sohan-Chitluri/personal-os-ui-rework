from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock
from uuid import UUID, uuid5

import pytest

from personal_os.sync.contracts import (
    Acknowledgement,
    AckStatus,
    ConflictRecord,
    ConflictStatus,
    EntitySnapshot,
    EntityType,
    ErrorCategory,
    OperationType,
    SyncOperation,
)
from personal_os.sync.engine import CommitResult, OperationReceipt, SyncEngine

_TEST_NAMESPACE = UUID("54a756e4-af79-48d4-b520-270ab9443d48")
_NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


class MemorySyncAdapter:
    """Small atomic adapter used to exercise the engine without a database."""

    def __init__(self) -> None:
        self.entities: dict[tuple[EntityType, str], EntitySnapshot] = {}
        self.history: dict[tuple[EntityType, str, int], EntitySnapshot] = {}
        self.conflicts: dict[UUID, ConflictRecord] = {}
        self.receipts_by_id: dict[UUID, OperationReceipt] = {}
        self.receipts_by_key: dict[str, OperationReceipt] = {}
        self.lock = Lock()

    def lookup_operation(
        self, operation_id: UUID, idempotency_key: str
    ) -> OperationReceipt | None:
        with self.lock:
            by_id = self.receipts_by_id.get(operation_id)
            by_key = self.receipts_by_key.get(idempotency_key)
            if by_id is not None and by_key is not None and by_id != by_key:
                # The engine will compare the chosen receipt's fingerprint; a
                # split identity cannot represent one valid original request.
                return OperationReceipt(
                    operation_id=operation_id,
                    idempotency_key=idempotency_key,
                    request_fingerprint="identity-collision",
                    acknowledgement=by_id.acknowledgement,
                )
            return by_id or by_key

    def get_current(
        self, entity_type: EntityType, entity_id: str
    ) -> EntitySnapshot | None:
        with self.lock:
            return self.entities.get((entity_type, entity_id))

    def get_revision(
        self, entity_type: EntityType, entity_id: str, revision: int
    ) -> EntitySnapshot | None:
        with self.lock:
            return self.history.get((entity_type, entity_id, revision))

    def get_conflict(self, conflict_id: UUID) -> ConflictRecord | None:
        with self.lock:
            return self.conflicts.get(conflict_id)

    def commit(
        self,
        *,
        operation: SyncOperation,
        request_fingerprint: str,
        expected_current: EntitySnapshot | None,
        check_current: bool,
        new_snapshot: EntitySnapshot | None,
        acknowledgement: Acknowledgement,
        conflict: ConflictRecord | None = None,
        resolve_conflict_id: UUID | None = None,
    ) -> CommitResult:
        with self.lock:
            by_id = self.receipts_by_id.get(operation.operation_id)
            by_key = self.receipts_by_key.get(operation.idempotency_key)
            prior = by_id or by_key
            if by_id is not None and by_key is not None and by_id != by_key:
                return CommitResult(idempotency_mismatch=True)
            if prior is not None:
                if prior.request_fingerprint != request_fingerprint:
                    return CommitResult(idempotency_mismatch=True)
                return CommitResult(replayed_ack=prior.acknowledgement)

            key = (operation.entity_type, operation.entity_id)
            if check_current and self.entities.get(key) != expected_current:
                return CommitResult(state_changed=True)

            if new_snapshot is not None:
                self.entities[key] = new_snapshot
                self.history[
                    (new_snapshot.entity_type, new_snapshot.entity_id, new_snapshot.revision)
                ] = new_snapshot
            if conflict is not None:
                self.conflicts[conflict.conflict_id] = conflict
            if resolve_conflict_id is not None:
                old_conflict = self.conflicts[resolve_conflict_id]
                self.conflicts[resolve_conflict_id] = old_conflict.model_copy(
                    update={
                        "status": ConflictStatus.RESOLVED,
                        "resolution_operation_id": operation.operation_id,
                    }
                )

            receipt = OperationReceipt(
                operation_id=operation.operation_id,
                idempotency_key=operation.idempotency_key,
                request_fingerprint=request_fingerprint,
                acknowledgement=acknowledgement,
            )
            self.receipts_by_id[operation.operation_id] = receipt
            self.receipts_by_key[operation.idempotency_key] = receipt
            return CommitResult(committed=True)

    def seed(self, snapshot: EntitySnapshot) -> None:
        with self.lock:
            key = (snapshot.entity_type, snapshot.entity_id)
            self.entities[key] = snapshot
            self.history[(snapshot.entity_type, snapshot.entity_id, snapshot.revision)] = snapshot

    def prune_revision(self, entity_type: EntityType, entity_id: str, revision: int) -> None:
        with self.lock:
            self.history.pop((entity_type, entity_id, revision), None)


def op(
    name: str,
    operation_type: OperationType,
    *,
    entity_id: str = "task-1",
    base_revision: int | None,
    payload: dict | None = None,
    resolves_conflict_id: UUID | None = None,
    entity_type: EntityType = EntityType.TASK,
    idempotency_key: str | None = None,
    depends_on: tuple[UUID, ...] = (),
) -> SyncOperation:
    operation_id = uuid5(_TEST_NAMESPACE, name)
    return SyncOperation(
        operation_id=operation_id,
        idempotency_key=idempotency_key or f"key-{name}",
        entity_type=entity_type,
        entity_id=entity_id,
        operation_type=operation_type,
        base_revision=base_revision,
        payload=payload,
        created_at=_NOW,
        depends_on=depends_on,
        resolves_conflict_id=resolves_conflict_id,
    )


def created(adapter: MemorySyncAdapter) -> EntitySnapshot:
    ack = SyncEngine(adapter).apply(
        op("create", OperationType.CREATE, base_revision=None, payload={"title": "base"})
    )
    assert ack.status == AckStatus.APPLIED
    snapshot = adapter.get_current(EntityType.TASK, "task-1")
    assert snapshot is not None
    return snapshot


def test_create_update_and_delete_advance_integer_revisions() -> None:
    adapter = MemorySyncAdapter()
    engine = SyncEngine(adapter)

    create_ack = engine.apply(
        op("create", OperationType.CREATE, base_revision=None, payload={"title": "one"})
    )
    update_ack = engine.apply(
        op("update", OperationType.UPDATE, base_revision=1, payload={"title": "two"})
    )
    delete_ack = engine.apply(op("delete", OperationType.DELETE, base_revision=2))

    assert (create_ack.status, create_ack.revision) == (AckStatus.APPLIED, 1)
    assert (update_ack.status, update_ack.revision) == (AckStatus.APPLIED, 2)
    assert (delete_ack.status, delete_ack.revision) == (AckStatus.APPLIED, 3)
    assert adapter.get_current(EntityType.TASK, "task-1") == EntitySnapshot(
        entity_type=EntityType.TASK,
        entity_id="task-1",
        revision=3,
        data=None,
        deleted=True,
    )


def test_concurrent_incompatible_updates_preserve_local_base_and_remote() -> None:
    adapter = MemorySyncAdapter()
    engine = SyncEngine(adapter)
    created(adapter)
    local = op("local-edit", OperationType.UPDATE, base_revision=1, payload={"title": "local"})
    remote = op("remote-edit", OperationType.UPDATE, base_revision=1, payload={"title": "remote"})

    assert engine.apply(remote).status == AckStatus.APPLIED
    ack = engine.apply(local)

    assert ack.status == AckStatus.CONFLICTED
    assert ack.error_category == ErrorCategory.STALE_REVISION
    conflict = adapter.get_conflict(ack.conflict_id)
    assert conflict is not None
    assert conflict.operation == local
    assert conflict.base_snapshot is not None and conflict.base_snapshot.revision == 1
    assert conflict.local_payload == {"title": "local"}
    assert conflict.remote_snapshot.revision == 2
    assert conflict.remote_snapshot.data == {"title": "remote"}
    assert adapter.get_current(EntityType.TASK, "task-1") == conflict.remote_snapshot


def test_duplicate_delivery_replays_ack_and_mismatched_id_or_key_is_rejected() -> None:
    adapter = MemorySyncAdapter()
    engine = SyncEngine(adapter)
    create = op("create", OperationType.CREATE, base_revision=None, payload={"title": "base"})
    original_ack = engine.apply(create)

    assert engine.apply(create) == original_ack
    assert len(adapter.receipts_by_id) == 1

    same_id_different_body = create.model_copy(update={"payload": {"title": "other"}})
    mismatch = engine.apply(same_id_different_body)
    assert mismatch.status == AckStatus.PERMANENT
    assert mismatch.error_category == ErrorCategory.IDEMPOTENCY_MISMATCH
    assert adapter.get_current(EntityType.TASK, "task-1").data == {"title": "base"}

    same_key_different_id = op(
        "other-id",
        OperationType.CREATE,
        base_revision=None,
        payload={"title": "different"},
        idempotency_key=create.idempotency_key,
    )
    mismatch = engine.apply(same_key_different_id)
    assert mismatch.status == AckStatus.PERMANENT
    assert mismatch.error_category == ErrorCategory.IDEMPOTENCY_MISMATCH


def test_delivery_reordered_before_its_base_is_retryable_then_applies() -> None:
    adapter = MemorySyncAdapter()
    engine = SyncEngine(adapter)
    created(adapter)
    first = op("first", OperationType.UPDATE, base_revision=1, payload={"title": "first"})
    second = op("second", OperationType.UPDATE, base_revision=2, payload={"title": "second"})

    early_ack = engine.apply(second)
    assert early_ack.status == AckStatus.RETRYABLE
    assert early_ack.error_category == ErrorCategory.MISSING_BASE
    assert second.operation_id not in adapter.receipts_by_id

    assert engine.apply(first).revision == 2
    final_ack = engine.apply(second)
    assert (final_ack.status, final_ack.revision) == (AckStatus.APPLIED, 3)
    assert adapter.get_current(EntityType.TASK, "task-1").data == {"title": "second"}


def test_update_reordered_before_its_dependent_create_remains_retryable() -> None:
    adapter = MemorySyncAdapter()
    engine = SyncEngine(adapter)
    create = op(
        "late-create",
        OperationType.CREATE,
        base_revision=None,
        payload={"title": "created"},
    )
    update = op(
        "early-update",
        OperationType.UPDATE,
        base_revision=1,
        payload={"title": "updated"},
        depends_on=(create.operation_id,),
    )

    early_ack = engine.apply(update)
    assert early_ack.status == AckStatus.RETRYABLE
    assert early_ack.error_category == ErrorCategory.MISSING_BASE
    assert update.operation_id not in adapter.receipts_by_id

    assert engine.apply(create).status == AckStatus.APPLIED
    retry_ack = engine.apply(update)
    assert (retry_ack.status, retry_ack.revision) == (AckStatus.APPLIED, 2)
    current = adapter.get_current(EntityType.TASK, "task-1")
    assert current is not None and current.data == {"title": "updated"}


def test_future_revision_after_tombstone_is_conflicted_instead_of_retried() -> None:
    adapter = MemorySyncAdapter()
    engine = SyncEngine(adapter)
    created(adapter)
    delete = op("delete-first", OperationType.DELETE, base_revision=1)
    assert engine.apply(delete).status == AckStatus.APPLIED

    later_update = op(
        "after-tombstone",
        OperationType.UPDATE,
        base_revision=4,
        payload={"title": "cannot resurrect"},
        depends_on=(delete.operation_id,),
    )
    acknowledgement = engine.apply(later_update)

    assert acknowledgement.status == AckStatus.CONFLICTED
    assert acknowledgement.error_category == ErrorCategory.MISSING_ENTITY
    current = adapter.get_current(EntityType.TASK, "task-1")
    assert current is not None and current.deleted is True


def test_stale_update_after_delete_keeps_tombstone_and_local_edit() -> None:
    adapter = MemorySyncAdapter()
    engine = SyncEngine(adapter)
    created(adapter)
    delete_ack = engine.apply(op("delete", OperationType.DELETE, base_revision=1))
    local = op("late-update", OperationType.UPDATE, base_revision=1, payload={"title": "offline"})

    ack = engine.apply(local)
    assert delete_ack.revision == 2
    assert ack.status == AckStatus.CONFLICTED
    conflict = adapter.get_conflict(ack.conflict_id)
    assert conflict is not None
    assert conflict.local_payload == {"title": "offline"}
    assert conflict.remote_snapshot.deleted is True
    assert conflict.remote_snapshot.revision == 2


def test_update_of_deleted_entity_at_exact_tombstone_revision_is_conflict() -> None:
    adapter = MemorySyncAdapter()
    engine = SyncEngine(adapter)
    created(adapter)
    engine.apply(op("delete", OperationType.DELETE, base_revision=1))

    ack = engine.apply(
        op("update-deleted", OperationType.UPDATE, base_revision=2, payload={"title": "resurrect"})
    )
    assert ack.status == AckStatus.CONFLICTED
    assert ack.error_category == ErrorCategory.MISSING_ENTITY
    assert adapter.get_current(EntityType.TASK, "task-1").deleted is True


def test_create_collision_and_missing_entity_are_explicit() -> None:
    adapter = MemorySyncAdapter()
    engine = SyncEngine(adapter)
    created(adapter)

    collision = engine.apply(
        op(
            "duplicate-create",
            OperationType.CREATE,
            base_revision=None,
            payload={"title": "second"},
        )
    )
    assert collision.status == AckStatus.CONFLICTED
    collision_record = adapter.get_conflict(collision.conflict_id)
    assert collision_record is not None
    assert collision_record.local_payload == {"title": "second"}
    assert collision_record.remote_snapshot.data == {"title": "base"}

    missing = engine.apply(
        op(
            "missing-update",
            OperationType.UPDATE,
            entity_id="absent",
            base_revision=1,
            payload={"x": 1},
        )
    )
    assert missing.status == AckStatus.PERMANENT
    assert missing.error_category == ErrorCategory.MISSING_ENTITY


def test_expired_base_conflict_keeps_local_and_current_without_guessing() -> None:
    adapter = MemorySyncAdapter()
    engine = SyncEngine(adapter)
    created(adapter)
    engine.apply(op("edit-2", OperationType.UPDATE, base_revision=1, payload={"title": "two"}))
    engine.apply(op("edit-3", OperationType.UPDATE, base_revision=2, payload={"title": "three"}))
    adapter.prune_revision(EntityType.TASK, "task-1", 1)

    stale = op("expired-base", OperationType.UPDATE, base_revision=1, payload={"title": "local"})
    ack = engine.apply(stale)
    conflict = adapter.get_conflict(ack.conflict_id)
    assert ack.status == AckStatus.CONFLICTED
    assert ack.error_category == ErrorCategory.EXPIRED_BASE
    assert conflict is not None
    assert conflict.base_snapshot is None
    assert conflict.local_payload == {"title": "local"}
    assert conflict.remote_snapshot.revision == 3
    assert conflict.remote_snapshot.data == {"title": "three"}


def test_future_base_is_retryable_without_receipt_even_after_reordering_repeats() -> None:
    adapter = MemorySyncAdapter()
    engine = SyncEngine(adapter)
    created(adapter)
    future = op("future", OperationType.UPDATE, base_revision=2, payload={"title": "future"})

    first = engine.apply(future)
    second = engine.apply(future)
    assert first == second
    assert first.status == AckStatus.RETRYABLE
    assert first.error_category == ErrorCategory.MISSING_BASE
    assert future.operation_id not in adapter.receipts_by_id


def test_unsupported_entity_and_operation_are_permanent_and_inspectable() -> None:
    adapter = MemorySyncAdapter()
    engine = SyncEngine(adapter, supported_entity_types=frozenset({EntityType.TASK}))
    project = op(
        "project-create",
        OperationType.CREATE,
        entity_type=EntityType.PROJECT,
        base_revision=None,
        payload={"name": "p"},
    )
    unsupported_entity = engine.apply(project)
    assert unsupported_entity.status == AckStatus.PERMANENT
    assert unsupported_entity.error_category == ErrorCategory.UNSUPPORTED

    unknown_operation = op(
        "archive",
        OperationType.UPDATE,
        base_revision=1,
        payload={"title": "x"},
    ).model_dump(mode="python")
    # Constructing a deliberately unknown enum value models a future protocol
    # payload which this version must reject instead of guessing at.
    unknown_operation["operation_type"] = "archive"
    unknown_operation = SyncOperation.model_construct(
        **unknown_operation
    )
    unsupported_op = engine.apply(unknown_operation)
    assert unsupported_op.status == AckStatus.PERMANENT
    assert unsupported_op.error_category == ErrorCategory.UNSUPPORTED


def test_resolution_is_revision_checked_idempotent_and_marks_conflict_resolved() -> None:
    adapter = MemorySyncAdapter()
    engine = SyncEngine(adapter)
    created(adapter)
    local = op("local", OperationType.UPDATE, base_revision=1, payload={"title": "local"})
    engine.apply(op("remote", OperationType.UPDATE, base_revision=1, payload={"title": "remote"}))
    conflict_ack = engine.apply(local)

    # The conflict was detected at revision 2, but the remote changed again
    # before the user's resolution reached the engine.
    engine.apply(
        op(
            "remote-again",
            OperationType.UPDATE,
            base_revision=2,
            payload={"title": "new remote"},
        )
    )
    stale_resolution = op(
        "stale-resolution",
        OperationType.RESOLVE_CONFLICT,
        base_revision=2,
        payload={"title": "chosen"},
        resolves_conflict_id=conflict_ack.conflict_id,
    )
    stale_ack = engine.apply(stale_resolution)
    assert stale_ack.status == AckStatus.CONFLICTED
    assert stale_ack.error_category == ErrorCategory.STALE_REVISION
    assert adapter.get_conflict(conflict_ack.conflict_id).status == ConflictStatus.UNRESOLVED

    resolution = op(
        "resolution",
        OperationType.RESOLVE_CONFLICT,
        base_revision=3,
        payload={"title": "chosen"},
        resolves_conflict_id=conflict_ack.conflict_id,
    )
    applied = engine.apply(resolution)
    assert (applied.status, applied.revision) == (AckStatus.APPLIED, 4)
    assert adapter.get_current(EntityType.TASK, "task-1").data == {"title": "chosen"}
    resolved = adapter.get_conflict(conflict_ack.conflict_id)
    assert resolved.status == ConflictStatus.RESOLVED
    assert resolved.resolution_operation_id == resolution.operation_id

    assert engine.apply(resolution) == applied
    repeated = op(
        "different-resolution",
        OperationType.RESOLVE_CONFLICT,
        base_revision=4,
        payload={"title": "other choice"},
        resolves_conflict_id=conflict_ack.conflict_id,
    )
    rejected = engine.apply(repeated)
    assert rejected.status == AckStatus.PERMANENT
    assert rejected.error_category == ErrorCategory.INVALID_OPERATION
    assert adapter.get_current(EntityType.TASK, "task-1").revision == 4


def test_resolution_unknown_conflict_and_wrong_entity_fail_without_mutation() -> None:
    adapter = MemorySyncAdapter()
    engine = SyncEngine(adapter)
    created(adapter)
    unknown = op(
        "unknown-resolution",
        OperationType.RESOLVE_CONFLICT,
        base_revision=1,
        payload={"title": "x"},
        resolves_conflict_id=uuid5(_TEST_NAMESPACE, "no-such-conflict"),
    )
    ack = engine.apply(unknown)
    assert ack.status == AckStatus.PERMANENT
    assert ack.error_category == ErrorCategory.INVALID_OPERATION
    assert adapter.get_current(EntityType.TASK, "task-1").revision == 1


def test_conflict_ids_are_deterministic_for_identical_state_and_operation() -> None:
    local = op("local", OperationType.UPDATE, base_revision=1, payload={"title": "local"})
    conflict_ids = []
    for _ in range(2):
        adapter = MemorySyncAdapter()
        engine = SyncEngine(adapter)
        created(adapter)
        engine.apply(
            op("remote", OperationType.UPDATE, base_revision=1, payload={"title": "remote"})
        )
        conflict_ids.append(engine.apply(local).conflict_id)
    assert conflict_ids[0] == conflict_ids[1]


def test_adapter_compare_and_commit_race_causes_reevaluation() -> None:
    class RacingAdapter(MemorySyncAdapter):
        race_once = False

        def commit(self, **kwargs) -> CommitResult:  # type: ignore[no-untyped-def]
            if self.race_once and kwargs["check_current"]:
                self.race_once = False
                current = kwargs["expected_current"]
                if current is not None:
                    newer = EntitySnapshot(
                        entity_type=current.entity_type,
                        entity_id=current.entity_id,
                        revision=current.revision + 1,
                        data={"title": "won-the-race"},
                        deleted=False,
                    )
                    self.seed(newer)
                return CommitResult(state_changed=True)
            return super().commit(**kwargs)

    adapter = RacingAdapter()
    engine = SyncEngine(adapter)
    created(adapter)
    adapter.race_once = True
    update = op("racy-update", OperationType.UPDATE, base_revision=1, payload={"title": "stale"})

    ack = engine.apply(update)
    assert ack.status == AckStatus.CONFLICTED
    assert ack.error_category == ErrorCategory.STALE_REVISION
    assert adapter.get_current(EntityType.TASK, "task-1").data == {"title": "won-the-race"}


@pytest.mark.parametrize(
    ("operation_type", "base_revision", "payload"),
    [
        (OperationType.UPDATE, 1, {"x": 1}),
        (OperationType.DELETE, 1, None),
    ],
)
def test_stale_operations_detect_current_revision(
    operation_type: OperationType, base_revision: int, payload: dict | None
) -> None:
    adapter = MemorySyncAdapter()
    engine = SyncEngine(adapter)
    created(adapter)
    engine.apply(op("advance", OperationType.UPDATE, base_revision=1, payload={"title": "new"}))

    ack = engine.apply(
        op("stale-param", operation_type, base_revision=base_revision, payload=payload)
    )
    assert ack.status == AckStatus.CONFLICTED
    assert ack.error_category == ErrorCategory.STALE_REVISION
