"""Deterministic, in-memory reconciliation simulator for the M3 contract.

The mock server deliberately has no HTTP, AWS, PostgreSQL, or device runtime
dependency. Its store models server-durable state separately from the server
object so a simulated restart preserves receipts, conflicts, revisions, and
change-feed history.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from personal_os.sync.contracts import (
    PROTOCOL_VERSION,
    Acknowledgement,
    AckStatus,
    ChangeFeedItem,
    ChangePage,
    ConflictRecord,
    ConflictStatus,
    EntitySnapshot,
    EntityType,
    ErrorCategory,
    OperationType,
    SyncCursor,
    SyncError,
    SyncOperation,
    Tombstone,
)
from personal_os.sync.engine import CommitResult, OperationReceipt, SyncEngine
from personal_os.sync.protocol import (
    MAX_CHANGE_PAGE_SIZE,
    MAX_UPLOAD_BATCH_SIZE,
    SUPPORTED_PROTOCOL_VERSIONS,
    ChangeFeedResponse,
    SnapshotResponse,
    UploadBatch,
    UploadResponse,
    VersionNegotiationRequest,
    VersionNegotiationResponse,
    expired_cursor_error,
    negotiate_versions,
    validate_upload_batch,
    validate_upload_response,
)

SIMULATION_START = datetime(2026, 1, 1, tzinfo=UTC)
_EVENT_NAMESPACE = UUID("b73d76f8-8218-4bc6-9a45-b247e833499b")
_CURSOR_NAMESPACE = UUID("970e13ec-0284-485b-98b8-48be803f0e27")

EntityKey = tuple[EntityType, str]


@dataclass(slots=True)
class _DurableMockStore:
    now: datetime = SIMULATION_START
    current: dict[EntityKey, EntitySnapshot] = field(default_factory=dict)
    history: dict[EntityKey, dict[int, EntitySnapshot]] = field(default_factory=dict)
    operation_receipts: dict[UUID, OperationReceipt] = field(default_factory=dict)
    key_receipts: dict[str, OperationReceipt] = field(default_factory=dict)
    conflicts: dict[UUID, ConflictRecord] = field(default_factory=dict)
    events: list[ChangeFeedItem] = field(default_factory=list)
    cursor_positions: dict[str, tuple[int, datetime]] = field(default_factory=dict)
    sequence: int = 0

    def lookup_operation(self, operation_id: UUID, idempotency_key: str) -> OperationReceipt | None:
        by_id = self.operation_receipts.get(operation_id)
        by_key = self.key_receipts.get(idempotency_key)
        if by_id is not None and by_id.idempotency_key == idempotency_key:
            return by_id
        if by_key is not None and by_key.operation_id == operation_id:
            return by_key
        return None

    def get_current(self, entity_type: EntityType, entity_id: str) -> EntitySnapshot | None:
        return self.current.get((entity_type, entity_id))

    def get_revision(
        self, entity_type: EntityType, entity_id: str, revision: int
    ) -> EntitySnapshot | None:
        return self.history.get((entity_type, entity_id), {}).get(revision)

    def get_conflict(self, conflict_id: UUID) -> ConflictRecord | None:
        return self.conflicts.get(conflict_id)

    def record_terminal_ack(
        self,
        operation: SyncOperation,
        acknowledgement: Acknowledgement,
    ) -> Acknowledgement:
        """Persist a terminal protocol-level acknowledgement idempotently."""

        canonical = json.dumps(
            operation.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        result = self.commit(
            operation=operation,
            request_fingerprint=fingerprint,
            expected_current=None,
            check_current=False,
            new_snapshot=None,
            acknowledgement=acknowledgement,
        )
        if result.replayed_ack is not None:
            return result.replayed_ack
        if result.idempotency_mismatch:
            return Acknowledgement(
                operation_id=operation.operation_id,
                status=AckStatus.PERMANENT,
                error_category=ErrorCategory.IDEMPOTENCY_MISMATCH,
                message="operation ID or idempotency key was already used with a different request",
            )
        if not result.committed:
            raise RuntimeError("terminal protocol acknowledgement was not persisted")
        return acknowledgement

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
        existing_id = self.operation_receipts.get(operation.operation_id)
        existing_key = self.key_receipts.get(operation.idempotency_key)
        for existing in (existing_id, existing_key):
            if existing is not None:
                if (
                    existing.operation_id == operation.operation_id
                    and existing.idempotency_key == operation.idempotency_key
                    and existing.request_fingerprint == request_fingerprint
                ):
                    return CommitResult(replayed_ack=existing.acknowledgement)
                return CommitResult(idempotency_mismatch=True)

        key = (operation.entity_type, operation.entity_id)
        if check_current and self.current.get(key) != expected_current:
            return CommitResult(state_changed=True)

        if resolve_conflict_id is not None:
            unresolved = self.conflicts.get(resolve_conflict_id)
            if unresolved is None or unresolved.status != ConflictStatus.UNRESOLVED:
                return CommitResult(state_changed=True)

        if new_snapshot is not None:
            if (
                new_snapshot.entity_type != operation.entity_type
                or new_snapshot.entity_id != operation.entity_id
            ):
                raise ValueError("committed snapshot identity must match the operation")
            self.current[key] = new_snapshot
            self.history.setdefault(key, {})[new_snapshot.revision] = new_snapshot
            self._append_event(new_snapshot)

        if conflict is not None:
            self.conflicts.setdefault(conflict.conflict_id, conflict)
        if resolve_conflict_id is not None:
            original = self.conflicts[resolve_conflict_id]
            self.conflicts[resolve_conflict_id] = original.model_copy(
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
        self.operation_receipts[operation.operation_id] = receipt
        self.key_receipts[operation.idempotency_key] = receipt
        return CommitResult(committed=True)

    def _append_event(self, snapshot: EntitySnapshot) -> None:
        self.sequence += 1
        event_id = uuid5(
            _EVENT_NAMESPACE,
            f"{self.sequence}:{snapshot.entity_type}:{snapshot.entity_id}",
        )
        if snapshot.deleted:
            tombstone = Tombstone(
                entity_type=snapshot.entity_type,
                entity_id=snapshot.entity_id,
                revision=snapshot.revision,
                sequence=self.sequence,
                deleted_at=self.now,
            )
            event = ChangeFeedItem(
                event_id=event_id,
                sequence=self.sequence,
                entity_type=snapshot.entity_type,
                entity_id=snapshot.entity_id,
                revision=snapshot.revision,
                tombstone=tombstone,
            )
        else:
            event = ChangeFeedItem(
                event_id=event_id,
                sequence=self.sequence,
                entity_type=snapshot.entity_type,
                entity_id=snapshot.entity_id,
                revision=snapshot.revision,
                snapshot=snapshot,
            )
        self.events.append(event)


class MockSyncServer:
    """Mock server with deterministic revision checks and durable receipts."""

    def __init__(
        self,
        *,
        now: datetime = SIMULATION_START,
        cursor_ttl: timedelta = timedelta(days=30),
        page_size: int = 100,
        supported_versions: tuple[int, ...] = SUPPORTED_PROTOCOL_VERSIONS,
        _store: _DurableMockStore | None = None,
    ) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("simulation time must include a timezone")
        if cursor_ttl.total_seconds() <= 0:
            raise ValueError("cursor_ttl must be positive")
        if not 1 <= page_size <= MAX_CHANGE_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_CHANGE_PAGE_SIZE}")
        if not supported_versions or any(version < 1 for version in supported_versions):
            raise ValueError("supported_versions must contain positive versions")
        if len(set(supported_versions)) != len(supported_versions):
            raise ValueError("supported_versions cannot contain duplicates")
        self.now = now.astimezone(UTC)
        self.cursor_ttl = cursor_ttl
        self.page_size = page_size
        self.supported_versions = supported_versions
        self._store = _store or _DurableMockStore(now=self.now)
        self._store.now = self.now
        self._engine = SyncEngine(self._store)
        # This is intentionally transient to demonstrate process restart.
        self.process_generation = 1

    def negotiate(self, request: VersionNegotiationRequest) -> VersionNegotiationResponse:
        return negotiate_versions(request, supported_versions=self.supported_versions)

    def upload(self, batch: UploadBatch) -> UploadResponse:
        error = validate_upload_batch(batch, supported_versions=self.supported_versions)
        if error is not None:
            return UploadResponse(
                protocol_version=PROTOCOL_VERSION,
                batch_id=batch.batch_id,
                error=error,
            )

        acknowledgements: list[Acknowledgement] = []
        for operation in batch.operations:
            dependency_receipts = [
                self._store.operation_receipts.get(dependency)
                for dependency in operation.depends_on
            ]
            pending_dependency = any(receipt is None for receipt in dependency_receipts)
            failed_dependency = any(
                receipt is not None
                and receipt.acknowledgement.status not in (AckStatus.APPLIED, AckStatus.DUPLICATE)
                for receipt in dependency_receipts
            )
            if failed_dependency:
                ack = Acknowledgement(
                    operation_id=operation.operation_id,
                    status=AckStatus.PERMANENT,
                    error_category=ErrorCategory.DEPENDENCY_FAILED,
                    message="a prerequisite operation has a terminal unsuccessful acknowledgement",
                )
                ack = self._store.record_terminal_ack(operation, ack)
            elif pending_dependency:
                ack = Acknowledgement(
                    operation_id=operation.operation_id,
                    status=AckStatus.RETRYABLE,
                    error_category=ErrorCategory.DEPENDENCY_PENDING,
                    message="one or more prerequisite operations have not been acknowledged yet",
                )
            else:
                ack = self._engine.apply(operation)
            acknowledgements.append(ack)

        return UploadResponse(
            protocol_version=batch.protocol_version,
            batch_id=batch.batch_id,
            acknowledgements=tuple(acknowledgements),
        )

    def current(self, entity_type: EntityType, entity_id: str) -> EntitySnapshot | None:
        return self._store.get_current(entity_type, entity_id)

    def conflicts(self, *, status: ConflictStatus | None = None) -> tuple[ConflictRecord, ...]:
        values = tuple(self._store.conflicts.values())
        if status is not None:
            values = tuple(conflict for conflict in values if conflict.status == status)
        return tuple(sorted(values, key=lambda conflict: str(conflict.conflict_id)))

    def issue_cursor(self, *, sequence: int | None = None) -> SyncCursor:
        position = self._store.sequence if sequence is None else sequence
        if not 0 <= position <= self._store.sequence:
            raise ValueError("cursor sequence must be within the current feed")
        expires_at = self.now + self.cursor_ttl
        token = "sc1_" + uuid5(_CURSOR_NAMESPACE, f"{position}:{expires_at.isoformat()}").hex
        self._store.cursor_positions[token] = (position, expires_at)
        return SyncCursor(value=token, expires_at=expires_at)

    def initial_cursor(self) -> SyncCursor:
        return self.issue_cursor(sequence=0)

    def changes(
        self,
        cursor: SyncCursor,
        *,
        page_size: int | None = None,
    ) -> ChangeFeedResponse:
        limit = self.page_size if page_size is None else page_size
        if not 1 <= limit <= MAX_CHANGE_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_CHANGE_PAGE_SIZE}")

        expiry_error = expired_cursor_error(cursor, now=self.now)
        registered = self._store.cursor_positions.get(cursor.value)
        if expiry_error is not None or registered is None or registered[1] != cursor.expires_at:
            error = expiry_error or SyncError(
                category=ErrorCategory.CURSOR_EXPIRED,
                retryable=False,
                message="change-feed cursor is unknown or no longer resumable",
            )
            return ChangeFeedResponse(protocol_version=PROTOCOL_VERSION, error=error)

        position, _ = registered
        available = [event for event in self._store.events if event.sequence > position]
        selected = available[:limit]
        next_position = selected[-1].sequence if selected else position
        next_cursor = self.issue_cursor(sequence=next_position)
        page = ChangePage(
            items=tuple(selected),
            next_cursor=next_cursor.value,
            has_more=len(available) > len(selected),
        )
        return ChangeFeedResponse(
            protocol_version=PROTOCOL_VERSION,
            page=page,
            cursor=next_cursor,
        )

    def snapshot(self) -> SnapshotResponse:
        """Capture all current snapshots and the matching feed head together."""

        snapshots = tuple(
            self._store.current[key]
            for key in sorted(self._store.current, key=lambda value: (value[0].value, value[1]))
        )
        return SnapshotResponse(
            protocol_version=PROTOCOL_VERSION,
            snapshots=snapshots,
            cursor=self.issue_cursor(sequence=self._store.sequence),
        )

    def advance_time(self, delta: timedelta) -> None:
        if delta.total_seconds() < 0:
            raise ValueError("simulation time cannot move backwards")
        self.now += delta
        self._store.now = self.now

    def restart(self) -> MockSyncServer:
        """Return a fresh process around the same durable mock state."""

        restarted = MockSyncServer(
            now=self.now,
            cursor_ttl=self.cursor_ttl,
            page_size=self.page_size,
            supported_versions=self.supported_versions,
            _store=self._store,
        )
        restarted.process_generation = self.process_generation + 1
        return restarted


class MockSyncClient:
    """Small queue and remote mirror used to exercise protocol resume rules."""

    def __init__(self, *, client_id: str = "sim-client") -> None:
        self.client_id = client_id
        self.operations: dict[UUID, SyncOperation] = {}
        self.pending: list[UUID] = []
        self.acknowledgements: dict[UUID, Acknowledgement] = {}
        self.batch_errors: list[SyncError] = []
        self.remote_mirror: dict[EntityKey, EntitySnapshot] = {}
        self.applied_event_ids: set[UUID] = set()
        self.cursor: SyncCursor | None = None
        self.negotiated_version: int | None = None

    def enqueue(self, operation: SyncOperation) -> None:
        existing = self.operations.get(operation.operation_id)
        if existing is not None and existing != operation:
            raise ValueError("operation ID was reused locally with a different body")
        self.operations[operation.operation_id] = operation
        if (
            operation.operation_id not in self.pending
            and operation.operation_id not in self.acknowledgements
        ):
            self.pending.append(operation.operation_id)

    def negotiate(
        self,
        server: MockSyncServer,
        versions: Iterable[int] = (1,),
    ) -> VersionNegotiationResponse:
        response = server.negotiate(
            VersionNegotiationRequest(
                client_id=self.client_id,
                supported_versions=tuple(versions),
            )
        )
        self.negotiated_version = response.selected_version
        return response

    def make_batch(self, *, limit: int = MAX_UPLOAD_BATCH_SIZE) -> UploadBatch:
        if not 1 <= limit <= MAX_UPLOAD_BATCH_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_UPLOAD_BATCH_SIZE}")
        version = self.negotiated_version or PROTOCOL_VERSION
        operations = tuple(self.operations[operation_id] for operation_id in self.pending[:limit])
        if not operations:
            raise ValueError("cannot create an upload batch without pending operations")
        return UploadBatch(
            protocol_version=version,
            batch_id=uuid5(_EVENT_NAMESPACE, ",".join(str(op.operation_id) for op in operations)),
            operations=operations,
        )

    def process_upload_response(self, batch: UploadBatch, response: UploadResponse) -> None:
        validate_upload_response(batch, response)
        if response.error is not None:
            self.batch_errors.append(response.error)
            return
        for acknowledgement in response.acknowledgements:
            self.acknowledgements[acknowledgement.operation_id] = acknowledgement
            if acknowledgement.status != AckStatus.RETRYABLE:
                self.pending = [
                    operation_id
                    for operation_id in self.pending
                    if operation_id != acknowledgement.operation_id
                ]

    def apply_snapshot(self, snapshot: SnapshotResponse) -> None:
        # Snapshot and cursor are one recovery boundary. The mirror is replaced
        # only after a complete new map is prepared.
        staged = {(entity.entity_type, entity.entity_id): entity for entity in snapshot.snapshots}
        self.remote_mirror = staged
        self.cursor = snapshot.cursor

    def apply_change_response(
        self,
        response: ChangeFeedResponse,
        *,
        interrupt_after_items: int | None = None,
    ) -> bool:
        """Apply items in order; interruption leaves the old cursor for replay.

        Individual item writes may have landed before an interruption. Replaying
        them is safe because the mirror compares entity revisions and event IDs.
        """

        if response.error is not None:
            self.batch_errors.append(response.error)
            return False
        assert response.page is not None and response.cursor is not None
        if interrupt_after_items is not None and interrupt_after_items < 0:
            raise ValueError("interrupt_after_items must be nonnegative")
        if interrupt_after_items == 0:
            return False

        for applied, item in enumerate(response.page.items, start=1):
            if item.event_id not in self.applied_event_ids:
                self._apply_change(item)
                self.applied_event_ids.add(item.event_id)
            if interrupt_after_items is not None and applied >= interrupt_after_items:
                return False

        self.cursor = response.cursor
        return True

    def _apply_change(self, item: ChangeFeedItem) -> None:
        if item.snapshot is not None:
            snapshot = item.snapshot
        else:
            assert item.tombstone is not None
            snapshot = EntitySnapshot(
                entity_type=item.entity_type,
                entity_id=item.entity_id,
                revision=item.revision,
                data=None,
                deleted=True,
            )
        key = (snapshot.entity_type, snapshot.entity_id)
        current = self.remote_mirror.get(key)
        if current is None or snapshot.revision > current.revision:
            self.remote_mirror[key] = snapshot

    def bootstrap(self, server: MockSyncServer) -> None:
        self.apply_snapshot(server.snapshot())

    def fetch_changes(
        self,
        server: MockSyncServer,
        *,
        interrupt_after_items: int | None = None,
    ) -> ChangeFeedResponse:
        if self.cursor is None:
            raise ValueError("client needs a snapshot before reading the change feed")
        response = server.changes(self.cursor)
        self.apply_change_response(response, interrupt_after_items=interrupt_after_items)
        return response

    def recover_expired_cursor(self, server: MockSyncServer) -> None:
        """Use full snapshot reconciliation before accepting a new feed cursor."""

        self.apply_snapshot(server.snapshot())


def make_operation(
    *,
    operation_id: UUID,
    entity_id: str,
    operation_type: OperationType,
    base_revision: int | None,
    payload: dict[str, object] | None,
    created_at: datetime = SIMULATION_START,
    depends_on: tuple[UUID, ...] = (),
    idempotency_key: str | None = None,
    entity_type: EntityType = EntityType.TASK,
    resolves_conflict_id: UUID | None = None,
) -> SyncOperation:
    """Test helper that keeps simulated operations fully explicit and stable."""

    return SyncOperation(
        operation_id=operation_id,
        idempotency_key=idempotency_key or f"idem-{operation_id}",
        entity_type=entity_type,
        entity_id=entity_id,
        operation_type=operation_type,
        base_revision=base_revision,
        payload=payload,
        created_at=created_at,
        depends_on=depends_on,
        resolves_conflict_id=resolves_conflict_id,
    )


__all__ = [
    "MockSyncClient",
    "MockSyncServer",
    "SIMULATION_START",
    "make_operation",
]
