"""Deterministic recovery and conflict scenarios for the M3 protocol simulator."""

from datetime import timedelta
from uuid import UUID, uuid5

from personal_os.sync.contracts import (
    AckStatus,
    ConflictStatus,
    EntityType,
    ErrorCategory,
    OperationType,
    SyncOperation,
)
from personal_os.sync.protocol import UploadBatch
from personal_os.sync.simulator import (
    SIMULATION_START,
    MockSyncClient,
    MockSyncServer,
    make_operation,
)

_NAMESPACE = UUID("c804cc2c-2fe5-4e1a-8207-34b9be66e050")


def uid(name: str) -> UUID:
    return uuid5(_NAMESPACE, name)


def op(
    name: str,
    entity_id: str,
    operation_type: OperationType,
    base_revision: int | None,
    payload: dict[str, object] | None,
    *,
    depends_on: tuple[UUID, ...] = (),
    resolves_conflict_id: UUID | None = None,
    created_at=SIMULATION_START,
) -> SyncOperation:
    return make_operation(
        operation_id=uid(name),
        entity_id=entity_id,
        operation_type=operation_type,
        base_revision=base_revision,
        payload=payload,
        depends_on=depends_on,
        resolves_conflict_id=resolves_conflict_id,
        created_at=created_at,
    )


def batch(name: str, *operations: SyncOperation, version: int = 1) -> UploadBatch:
    return UploadBatch(
        protocol_version=version,
        batch_id=uid(f"batch:{name}"),
        operations=operations,
    )


def create(server: MockSyncServer, entity_id: str, name: str | None = None) -> SyncOperation:
    operation = op(
        name or f"create:{entity_id}",
        entity_id,
        OperationType.CREATE,
        None,
        {"title": f"initial {entity_id}"},
    )
    response = server.upload(batch(f"create:{entity_id}", operation))
    assert response.acknowledgements[0].status == AckStatus.APPLIED
    return operation


def test_extended_offline_period_exposes_concurrent_conflict_and_expired_cursor_recovery():
    server = MockSyncServer(cursor_ttl=timedelta(days=30))
    client = MockSyncClient()
    assert client.negotiate(server).selected_version == 1

    initial = op("offline-create", "task-1", OperationType.CREATE, None, {"title": "base"})
    client.enqueue(initial)
    create_batch = client.make_batch()
    client.process_upload_response(create_batch, server.upload(create_batch))
    client.bootstrap(server)
    original_cursor = client.cursor
    assert client.remote_mirror[(EntityType.TASK, "task-1")].revision == 1

    # The client stays offline for three months while the cloud continues to move.
    server.advance_time(timedelta(days=91))
    local = op(
        "offline-edit",
        "task-1",
        OperationType.UPDATE,
        1,
        {"title": "offline version"},
        created_at=server.now,
    )
    client.enqueue(local)
    remote = op(
        "cloud-edit",
        "task-1",
        OperationType.UPDATE,
        1,
        {"title": "cloud version"},
        created_at=server.now,
    )
    remote_ack = server.upload(batch("cloud", remote)).acknowledgements[0]
    assert remote_ack.status == AckStatus.APPLIED
    assert remote_ack.revision == 2

    expired = client.fetch_changes(server)
    assert expired.error is not None
    assert expired.error.category == ErrorCategory.CURSOR_EXPIRED
    assert client.cursor == original_cursor
    assert client.remote_mirror[(EntityType.TASK, "task-1")].revision == 1

    # Expiry recovery first applies a complete snapshot and its matching feed head.
    client.recover_expired_cursor(server)
    assert client.remote_mirror[(EntityType.TASK, "task-1")].data == {"title": "cloud version"}
    assert client.cursor is not None and client.cursor.expires_at > server.now

    after_snapshot = op(
        "cloud-after-snapshot",
        "task-1",
        OperationType.UPDATE,
        2,
        {"title": "cloud after snapshot"},
        created_at=server.now,
    )
    assert (
        server.upload(batch("cloud-after-snapshot", after_snapshot)).acknowledgements[0].revision
        == 3
    )
    assert client.fetch_changes(server).page is not None
    assert client.remote_mirror[(EntityType.TASK, "task-1")].data == {
        "title": "cloud after snapshot"
    }

    local_batch = client.make_batch()
    local_response = server.upload(local_batch)
    client.process_upload_response(local_batch, local_response)
    local_ack = client.acknowledgements[local.operation_id]
    assert local_ack.status == AckStatus.CONFLICTED
    conflict = server.conflicts(status=ConflictStatus.UNRESOLVED)[0]
    assert conflict.operation == local
    assert conflict.local_payload == {"title": "offline version"}
    assert conflict.remote_snapshot.data == {"title": "cloud after snapshot"}
    assert server.current(EntityType.TASK, "task-1").revision == 3
    assert local.operation_id not in client.pending
    assert client.operations[local.operation_id] == local


def test_timeout_duplicate_upload_and_server_restart_replay_one_durable_result():
    server = MockSyncServer()
    client = MockSyncClient()
    assert client.negotiate(server).selected_version == 1
    operation = op("timeout-create", "task-timeout", OperationType.CREATE, None, {"title": "once"})
    client.enqueue(operation)
    request = client.make_batch()

    # The server commits but the simulated response is lost before client ack persistence.
    first_response = server.upload(request)
    assert first_response.acknowledgements[0].status == AckStatus.APPLIED
    assert client.pending == [operation.operation_id]
    assert server.current(EntityType.TASK, "task-timeout").revision == 1
    assert len(server._store.events) == 1

    restarted = server.restart()
    assert restarted.process_generation == server.process_generation + 1
    replay = restarted.upload(request)
    assert replay.acknowledgements == first_response.acknowledgements
    client.process_upload_response(request, replay)
    assert client.pending == []
    assert restarted.current(EntityType.TASK, "task-timeout").revision == 1
    assert len(restarted._store.events) == 1

    # Reusing either idempotency identity with a different canonical body is rejected.
    changed = operation.model_copy(update={"payload": {"title": "different"}})
    mismatch = restarted.upload(batch("idempotency-mismatch", changed)).acknowledgements[0]
    assert mismatch.status == AckStatus.PERMANENT
    assert mismatch.error_category == ErrorCategory.IDEMPOTENCY_MISMATCH
    assert len(restarted._store.events) == 1


def test_reordered_delivery_missing_base_then_partial_batch_success():
    server = MockSyncServer()
    create(server, "task-order")
    first = op("order-first", "task-order", OperationType.UPDATE, 1, {"title": "revision two"})
    second = op("order-second", "task-order", OperationType.UPDATE, 2, {"title": "revision three"})

    early = server.upload(batch("early-second", second)).acknowledgements[0]
    assert early.status == AckStatus.RETRYABLE
    assert early.error_category == ErrorCategory.MISSING_BASE
    assert second.operation_id not in server._store.operation_receipts

    first_ack = server.upload(batch("first", first)).acknowledgements[0]
    second_ack = server.upload(batch("second", second)).acknowledgements[0]
    assert first_ack.status == AckStatus.APPLIED and first_ack.revision == 2
    assert second_ack.status == AckStatus.APPLIED and second_ack.revision == 3
    assert server.current(EntityType.TASK, "task-order").data == {"title": "revision three"}

    valid_create = op("partial-valid", "task-valid", OperationType.CREATE, None, {"title": "valid"})
    invalid_update = op(
        "partial-invalid", "missing-task", OperationType.UPDATE, 1, {"title": "bad"}
    )
    partial = server.upload(batch("partial", valid_create, invalid_update))
    assert [ack.status for ack in partial.acknowledgements] == [
        AckStatus.APPLIED,
        AckStatus.PERMANENT,
    ]
    assert partial.acknowledgements[1].error_category == ErrorCategory.MISSING_ENTITY
    assert server.current(EntityType.TASK, "task-valid").data == {"title": "valid"}


def test_declared_dependency_retry_is_not_receipted_until_prerequisite_succeeds():
    server = MockSyncServer()
    prerequisite = op(
        "prerequisite", "task-prerequisite", OperationType.CREATE, None, {"title": "first"}
    )
    dependent = op(
        "dependent",
        "task-dependent",
        OperationType.CREATE,
        None,
        {"title": "second"},
        depends_on=(prerequisite.operation_id,),
    )
    failed = server.upload(batch("dependency-early", dependent)).acknowledgements[0]
    assert failed.status == AckStatus.RETRYABLE
    assert failed.error_category == ErrorCategory.DEPENDENCY_PENDING
    assert dependent.operation_id not in server._store.operation_receipts

    assert (
        server.upload(batch("dependency-first", prerequisite)).acknowledgements[0].status
        == AckStatus.APPLIED
    )
    retried = server.upload(batch("dependency-later", dependent)).acknowledgements[0]
    assert retried.status == AckStatus.APPLIED
    assert server.current(EntityType.TASK, "task-dependent").revision == 1


def test_terminal_prerequisite_failure_permanently_fails_dependent_idempotently():
    server = MockSyncServer()
    prerequisite = op(
        "terminal-prerequisite",
        "missing-prerequisite-entity",
        OperationType.UPDATE,
        1,
        {"title": "cannot update"},
    )
    prerequisite_ack = server.upload(batch("terminal-prerequisite", prerequisite)).acknowledgements[
        0
    ]
    assert prerequisite_ack.status == AckStatus.PERMANENT

    dependent = op(
        "terminal-dependent",
        "task-terminal-dependent",
        OperationType.CREATE,
        None,
        {"title": "must not run"},
        depends_on=(prerequisite.operation_id,),
    )
    request = batch("terminal-dependent", dependent)
    failed = server.upload(request).acknowledgements[0]
    assert failed.status == AckStatus.PERMANENT
    assert failed.error_category == ErrorCategory.DEPENDENCY_FAILED
    assert dependent.operation_id in server._store.operation_receipts

    repeated = server.upload(request).acknowledgements[0]
    assert repeated == failed
    assert server.current(EntityType.TASK, "task-terminal-dependent") is None


def test_tombstone_propagates_through_feed_and_replayed_page_is_safe():
    server = MockSyncServer(page_size=1)
    create(server, "task-delete")
    client = MockSyncClient()
    client.bootstrap(server)
    cursor_before = client.cursor

    delete = op("delete-task", "task-delete", OperationType.DELETE, 1, None)
    assert server.upload(batch("delete", delete)).acknowledgements[0].status == AckStatus.APPLIED
    response = server.changes(cursor_before)
    assert response.page is not None
    assert response.page.items[0].tombstone is not None

    # The first item write lands, but the cursor does not advance on interruption.
    assert client.apply_change_response(response, interrupt_after_items=1) is False
    assert client.cursor == cursor_before
    mirrored = client.remote_mirror[(EntityType.TASK, "task-delete")]
    assert mirrored.deleted is True and mirrored.data is None
    replay = server.changes(client.cursor)
    assert client.apply_change_response(replay) is True
    assert client.cursor == replay.cursor
    assert len(client.applied_event_ids) == 1


def test_protocol_mismatch_keeps_client_queue_and_server_state_untouched():
    server = MockSyncServer()
    client = MockSyncClient()
    mismatch = client.negotiate(server, versions=(2,))
    assert mismatch.selected_version is None
    assert mismatch.error is not None
    assert mismatch.error.category == ErrorCategory.PROTOCOL_MISMATCH

    queued = op("mismatch-queued", "task-mismatch", OperationType.CREATE, None, {"title": "queued"})
    client.enqueue(queued)
    request = batch("mismatch", queued, version=2)
    response = server.upload(request)
    assert response.error is not None
    assert response.error.category == ErrorCategory.PROTOCOL_MISMATCH
    assert response.protocol_version == 1
    client.process_upload_response(request, response)

    assert client.pending == [queued.operation_id]
    assert client.operations[queued.operation_id] == queued
    assert client.acknowledgements == {}
    assert server.current(EntityType.TASK, "task-mismatch") is None
    assert server._store.operation_receipts == {}


def test_resolution_is_idempotent_and_later_concurrent_update_creates_new_conflict():
    server = MockSyncServer()
    create(server, "task-resolve")
    cloud = op("resolve-cloud", "task-resolve", OperationType.UPDATE, 1, {"title": "cloud"})
    assert server.upload(batch("resolve-cloud", cloud)).acknowledgements[0].revision == 2

    local = op("resolve-local", "task-resolve", OperationType.UPDATE, 1, {"title": "offline"})
    local_ack = server.upload(batch("resolve-local", local)).acknowledgements[0]
    assert local_ack.status == AckStatus.CONFLICTED
    assert local_ack.conflict_id is not None
    conflict_id = local_ack.conflict_id

    resolution = op(
        "resolution",
        "task-resolve",
        OperationType.RESOLVE_CONFLICT,
        2,
        {"title": "manually reconciled"},
        resolves_conflict_id=conflict_id,
    )
    resolution_batch = batch("resolution", resolution)
    resolution_ack = server.upload(resolution_batch).acknowledgements[0]
    assert resolution_ack.status == AckStatus.APPLIED
    assert resolution_ack.revision == 3
    assert server.conflicts()[0].status == ConflictStatus.RESOLVED

    later_writer = op(
        "later-writer", "task-resolve", OperationType.UPDATE, 2, {"title": "concurrent"}
    )
    later_ack = server.upload(batch("later-writer", later_writer)).acknowledgements[0]
    assert later_ack.status == AckStatus.CONFLICTED
    assert later_ack.conflict_id != conflict_id
    unresolved = server.conflicts(status=ConflictStatus.UNRESOLVED)
    assert len(unresolved) == 1
    assert unresolved[0].local_payload == {"title": "concurrent"}
    assert unresolved[0].remote_snapshot.data == {"title": "manually reconciled"}
    assert server.current(EntityType.TASK, "task-resolve").revision == 3

    repeated = server.upload(resolution_batch).acknowledgements[0]
    assert repeated == resolution_ack
    assert len(server._store.events) == 3
    another_resolution = op(
        "different-resolution",
        "task-resolve",
        OperationType.RESOLVE_CONFLICT,
        3,
        {"title": "should not apply"},
        resolves_conflict_id=conflict_id,
    )
    rejected = server.upload(batch("different-resolution", another_resolution)).acknowledgements[0]
    assert rejected.status == AckStatus.PERMANENT
    assert rejected.error_category == ErrorCategory.INVALID_OPERATION
    assert server.current(EntityType.TASK, "task-resolve").data == {"title": "manually reconciled"}


def test_feed_cursor_advances_only_after_page_application_and_replay_does_not_skip():
    server = MockSyncServer(page_size=1)
    client = MockSyncClient()
    client.bootstrap(server)
    starting_cursor = client.cursor
    first = op("feed-first", "feed-one", OperationType.CREATE, None, {"title": "one"})
    second = op("feed-second", "feed-two", OperationType.CREATE, None, {"title": "two"})
    batch_response = server.upload(batch("feed-two", first, second))
    assert [ack.status for ack in batch_response.acknowledgements] == [
        AckStatus.APPLIED,
        AckStatus.APPLIED,
    ]

    first_page = server.changes(starting_cursor)
    assert first_page.page is not None and first_page.page.has_more
    assert client.apply_change_response(first_page, interrupt_after_items=1) is False
    assert client.cursor == starting_cursor
    assert (EntityType.TASK, "feed-one") in client.remote_mirror

    # Resume from the prior cursor: the first event is replayed, then the cursor advances.
    replayed_page = server.changes(client.cursor)
    assert client.apply_change_response(replayed_page) is True
    assert client.cursor == replayed_page.cursor
    second_page = server.changes(client.cursor)
    assert second_page.page is not None
    assert [item.entity_id for item in second_page.page.items] == ["feed-two"]
    assert client.apply_change_response(second_page) is True
    assert client.remote_mirror[(EntityType.TASK, "feed-two")].data == {"title": "two"}
