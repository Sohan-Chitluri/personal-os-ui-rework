"""PostgreSQL persistence for versioned sync operations.

This adapter is intentionally not wired to an API route. It applies the
existing deterministic sync engine to canonical PostgreSQL task/project rows
and commits each accepted operation, receipt, revision snapshot, conflict,
change event, and tombstone in one PostgreSQL transaction.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Uuid,
    or_,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from personal_os.db import models as db_models
from personal_os.db.base import Base, TZDateTime, ULIDType, utcnow
from personal_os.sync.contracts import (
    PROTOCOL_VERSION,
    Acknowledgement,
    AckStatus,
    ChangeFeedItem,
    ConflictRecord,
    ConflictStatus,
    EntitySnapshot,
    EntityType,
    ErrorCategory,
    SyncOperation,
    Tombstone,
)
from personal_os.sync.engine import (
    CommitResult,
    OperationReceipt,
    SyncEngine,
)


class SyncOperationReceipt(Base):
    """Durable idempotency result for an accepted or terminal operation."""

    __tablename__ = "sync_operation_receipts"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[str] = mapped_column(
        ULIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    operation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    operation_envelope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    acknowledgement: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (
        Index("uq_sync_receipts_owner_operation", "owner_id", "operation_id", unique=True),
        Index("uq_sync_receipts_owner_idempotency", "owner_id", "idempotency_key", unique=True),
    )


class SyncEntityRevision(Base):
    """Historical canonical snapshots retained as trustworthy sync bases."""

    __tablename__ = "sync_entity_revisions"

    owner_id: Mapped[str] = mapped_column(
        ULIDType,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(32), primary_key=True, nullable=False)
    entity_id: Mapped[str] = mapped_column(String(200), primary_key=True, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )


class SyncConflict(Base):
    """Durable conflict record containing local, remote, and common-base data."""

    __tablename__ = "sync_conflicts"

    conflict_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        ULIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    record: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    resolution_operation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))

    __table_args__ = (
        Index("ix_sync_conflicts_owner_status", "owner_id", "status"),
        Index("ix_sync_conflicts_owner_entity", "owner_id", "entity_type", "entity_id"),
    )


class SyncChangeEvent(Base):
    """Append-only, owner-scoped server change feed."""

    __tablename__ = "sync_change_events"

    sequence: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), Identity(), primary_key=True
    )
    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, unique=True)
    owner_id: Mapped[str] = mapped_column(
        ULIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(200), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    tombstone: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (
        Index("ix_sync_change_events_owner_sequence", "owner_id", "sequence"),
        Index(
            "uq_sync_change_events_owner_entity_revision",
            "owner_id",
            "entity_type",
            "entity_id",
            "revision",
            unique=True,
        ),
    )


class SyncTombstone(Base):
    """Latest deletion revision for an entity; deletion remains explicit."""

    __tablename__ = "sync_tombstones"

    owner_id: Mapped[str] = mapped_column(
        ULIDType,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(32), primary_key=True, nullable=False)
    entity_id: Mapped[str] = mapped_column(String(200), primary_key=True, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sync_change_events.sequence", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sync_change_events.event_id", ondelete="CASCADE"),
        nullable=False,
    )
    deleted_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)


@dataclass(frozen=True, slots=True)
class _PendingCommit:
    operation: SyncOperation
    fingerprint: str
    expected_current: EntitySnapshot | None
    new_snapshot: EntitySnapshot | None
    acknowledgement: Acknowledgement
    conflict: ConflictRecord | None
    resolve_conflict_id: UUID | None


class _UnitOfWorkAdapter:
    """Synchronous in-memory view used only inside one locked DB transaction."""

    def __init__(
        self,
        current: EntitySnapshot | None,
        revisions: dict[int, EntitySnapshot],
        conflicts: dict[UUID, ConflictRecord],
    ) -> None:
        self.current = current
        self.revisions = revisions
        self.conflicts = conflicts
        self.pending: _PendingCommit | None = None

    def lookup_operation(
        self, operation_id: UUID, idempotency_key: str
    ) -> OperationReceipt | None:
        return None

    def get_current(
        self, entity_type: EntityType, entity_id: str
    ) -> EntitySnapshot | None:
        current = self.current
        if current is None or current.entity_type != entity_type or current.entity_id != entity_id:
            return None
        return current

    def get_revision(
        self, entity_type: EntityType, entity_id: str, revision: int
    ) -> EntitySnapshot | None:
        value = self.revisions.get(revision)
        if value is None or value.entity_type != entity_type or value.entity_id != entity_id:
            return None
        return value

    def get_conflict(self, conflict_id: UUID) -> ConflictRecord | None:
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
        if check_current and self.current != expected_current:
            return CommitResult(state_changed=True)
        if self.pending is not None:
            raise RuntimeError("sync engine attempted more than one commit in a unit of work")
        self.pending = _PendingCommit(
            operation=operation,
            fingerprint=request_fingerprint,
            expected_current=expected_current,
            new_snapshot=new_snapshot,
            acknowledgement=acknowledgement,
            conflict=conflict,
            resolve_conflict_id=resolve_conflict_id,
        )
        if new_snapshot is not None:
            self.current = new_snapshot
            self.revisions[new_snapshot.revision] = new_snapshot
        if conflict is not None:
            self.conflicts[conflict.conflict_id] = conflict
        if resolve_conflict_id is not None:
            existing = self.conflicts[resolve_conflict_id]
            self.conflicts[resolve_conflict_id] = existing.model_copy(
                update={
                    "status": ConflictStatus.RESOLVED,
                    "resolution_operation_id": operation.operation_id,
                }
            )
        return CommitResult(committed=True)


class PostgresSyncStore:
    """Persist sync operations against PostgreSQL canonical task/project rows.

    ``session_factory`` must be bound to PostgreSQL. This class rejects other
    dialects rather than providing an SQLite fallback. Callers supply the
    authenticated owner's ID; this store is not yet exposed through an API.
    """

    _ENTITY_MODELS: dict[EntityType, Any] = {
        EntityType.TASK: db_models.Task,
        EntityType.PROJECT: db_models.Project,
    }
    _SYNC_FIELDS = {
        EntityType.TASK: {
            "title",
            "description",
            "status",
            "priority",
            "due_date",
            "project_id",
            "capture_id",
        },
        EntityType.PROJECT: {"name", "description", "status"},
    }
    _REQUIRED_FIELDS = {EntityType.TASK: "title", EntityType.PROJECT: "name"}

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        bind = session_factory.kw.get("bind")
        if bind is None or bind.dialect.name != "postgresql":
            raise ValueError("PostgresSyncStore requires a PostgreSQL session factory")
        self._session_factory = session_factory

    async def apply_operation(
        self, owner_id: str, operation: SyncOperation
    ) -> Acknowledgement:
        """Apply one operation and commit all sync state atomically."""
        fingerprint = _operation_fingerprint(operation)
        async with self._session_factory() as session, session.begin():
            await self._lock_operation(session, owner_id, operation)
            receipt = await self._find_receipt(session, owner_id, operation)
            if receipt is not None:
                if isinstance(receipt, _ReceiptMismatch):
                    return self._idempotency_mismatch(operation)
                if receipt.request_fingerprint == fingerprint:
                    return Acknowledgement.model_validate(receipt.acknowledgement)
                return self._idempotency_mismatch(operation)

            dependency_ack = await self._check_dependencies(
                session, owner_id, operation
            )
            if dependency_ack is not None:
                if dependency_ack.status == AckStatus.PERMANENT:
                    await self._record_receipt(
                        session, owner_id, operation, fingerprint, dependency_ack
                    )
                return dependency_ack

            if operation.protocol_version != PROTOCOL_VERSION:
                acknowledgement = self._permanent(
                    operation,
                    ErrorCategory.PROTOCOL_MISMATCH,
                    "unsupported sync protocol version",
                )
                await self._record_receipt(
                    session, owner_id, operation, fingerprint, acknowledgement
                )
                return acknowledgement

            current_row = None
            if operation.entity_type in self._ENTITY_MODELS:
                if not _is_ulid(operation.entity_id):
                    acknowledgement = self._permanent(
                        operation,
                        ErrorCategory.INVALID_OPERATION,
                        "task and project IDs must be ULIDs in the current PostgreSQL schema",
                    )
                    await self._record_receipt(
                        session, owner_id, operation, fingerprint, acknowledgement
                    )
                    return acknowledgement
                payload_error = self._payload_error(operation)
                if payload_error is not None:
                    acknowledgement = self._permanent(
                        operation, ErrorCategory.INVALID_OPERATION, payload_error
                    )
                    await self._record_receipt(
                        session, owner_id, operation, fingerprint, acknowledgement
                    )
                    return acknowledgement
                reference_error = await self._reference_error(
                    session, owner_id, operation
                )
                if reference_error is not None:
                    acknowledgement = self._permanent(
                        operation, ErrorCategory.INVALID_OPERATION, reference_error
                    )
                    await self._record_receipt(
                        session, owner_id, operation, fingerprint, acknowledgement
                    )
                    return acknowledgement
                current_row = await self._load_entity(session, owner_id, operation)

            current = _snapshot_from_row(operation.entity_type, operation.entity_id, current_row)
            history = await self._load_revisions(
                session, owner_id, operation.entity_type, operation.entity_id
            )
            if current is not None:
                history.setdefault(current.revision, current)
            conflicts = await self._load_conflicts(
                session, owner_id, operation.entity_type, operation.entity_id
            )
            adapter = _UnitOfWorkAdapter(current, history, conflicts)
            engine = SyncEngine(
                adapter,
                supported_entity_types=frozenset(self._ENTITY_MODELS),
            )
            acknowledgement = engine.apply(operation)
            pending = adapter.pending
            if pending is None:
                return acknowledgement

            if pending.expected_current is not None:
                await self._record_revision(
                    session, owner_id, pending.expected_current
                )
            if pending.new_snapshot is not None:
                await self._persist_entity(
                    session,
                    owner_id,
                    current_row,
                    pending.new_snapshot,
                )
                await self._record_revision(session, owner_id, pending.new_snapshot)
                await self._append_change(session, owner_id, pending.new_snapshot)

            if pending.conflict is not None:
                session.add(
                    SyncConflict(
                        conflict_id=pending.conflict.conflict_id,
                        owner_id=owner_id,
                        entity_type=pending.conflict.operation.entity_type.value,
                        entity_id=pending.conflict.operation.entity_id,
                        status=pending.conflict.status.value,
                        record=pending.conflict.model_dump(mode="json"),
                        created_at=pending.conflict.created_at,
                    )
                )

            if pending.resolve_conflict_id is not None:
                conflict_row = await session.get(
                    SyncConflict, pending.resolve_conflict_id
                )
                if conflict_row is None or conflict_row.owner_id != owner_id:
                    raise RuntimeError("resolved conflict disappeared during locked commit")
                conflict_row.status = ConflictStatus.RESOLVED.value
                conflict_row.resolution_operation_id = operation.operation_id
                conflict_row.record = adapter.conflicts[
                    pending.resolve_conflict_id
                ].model_dump(mode="json")

            await self._record_receipt(
                session, owner_id, operation, fingerprint, acknowledgement
            )
            await session.flush()
            return acknowledgement

    async def list_changes(
        self, owner_id: str, *, after_sequence: int = 0, limit: int = 100
    ) -> tuple[ChangeFeedItem, ...]:
        """Read a bounded, ordered PostgreSQL change-feed page by sequence."""
        if after_sequence < 0:
            raise ValueError("after_sequence must be nonnegative")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(SyncChangeEvent)
                    .where(
                        SyncChangeEvent.owner_id == owner_id,
                        SyncChangeEvent.sequence > after_sequence,
                    )
                    .order_by(SyncChangeEvent.sequence)
                    .limit(limit)
                )
            ).scalars()
            items: list[ChangeFeedItem] = []
            for row in rows:
                items.append(
                    ChangeFeedItem(
                        event_id=row.event_id,
                        sequence=row.sequence,
                        entity_type=EntityType(row.entity_type),
                        entity_id=row.entity_id,
                        revision=row.revision,
                        snapshot=(
                            EntitySnapshot.model_validate(row.snapshot)
                            if row.snapshot is not None
                            else None
                        ),
                        tombstone=(
                            Tombstone.model_validate(row.tombstone)
                            if row.tombstone is not None
                            else None
                        ),
                    )
                )
            return tuple(items)

    async def get_conflict(self, owner_id: str, conflict_id: UUID) -> ConflictRecord | None:
        async with self._session_factory() as session:
            row = await session.get(SyncConflict, conflict_id)
            if row is None or row.owner_id != owner_id:
                return None
            return ConflictRecord.model_validate(row.record)

    async def _lock_operation(
        self, session: AsyncSession, owner_id: str, operation: SyncOperation
    ) -> None:
        keys = (
            f"entity:{owner_id}:{operation.entity_type.value}:{operation.entity_id}",
            f"idempotency:{owner_id}:{operation.idempotency_key}",
            f"operation:{owner_id}:{operation.operation_id}",
        )
        for key in sorted(keys):
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": key},
            )

    async def _find_receipt(
        self, session: AsyncSession, owner_id: str, operation: SyncOperation
    ) -> SyncOperationReceipt | _ReceiptMismatch | None:
        rows = (
            await session.execute(
                select(SyncOperationReceipt).where(
                    SyncOperationReceipt.owner_id == owner_id,
                    or_(
                        SyncOperationReceipt.operation_id == operation.operation_id,
                        SyncOperationReceipt.idempotency_key == operation.idempotency_key,
                    ),
                )
            )
        ).scalars().all()
        if not rows:
            return None
        if (
            len(rows) != 1
            or rows[0].operation_id != operation.operation_id
            or rows[0].idempotency_key != operation.idempotency_key
        ):
            return _ReceiptMismatch()
        return rows[0]

    async def _check_dependencies(
        self,
        session: AsyncSession,
        owner_id: str,
        operation: SyncOperation,
    ) -> Acknowledgement | None:
        if not operation.depends_on:
            return None
        rows = (
            await session.execute(
                select(SyncOperationReceipt).where(
                    SyncOperationReceipt.owner_id == owner_id,
                    SyncOperationReceipt.operation_id.in_(operation.depends_on),
                )
            )
        ).scalars()
        receipts = {row.operation_id: row for row in rows}
        if any(dependency not in receipts for dependency in operation.depends_on):
            return Acknowledgement(
                operation_id=operation.operation_id,
                status=AckStatus.RETRYABLE,
                error_category=ErrorCategory.DEPENDENCY_PENDING,
                message="one or more prerequisite operations have not been acknowledged yet",
            )
        for dependency in operation.depends_on:
            acknowledgement = Acknowledgement.model_validate(
                receipts[dependency].acknowledgement
            )
            if acknowledgement.status not in (AckStatus.APPLIED, AckStatus.DUPLICATE):
                return self._permanent(
                    operation,
                    ErrorCategory.DEPENDENCY_FAILED,
                    "a prerequisite operation has a terminal unsuccessful acknowledgement",
                )
        return None

    async def _load_entity(
        self, session: AsyncSession, owner_id: str, operation: SyncOperation
    ) -> Any | None:
        model = self._ENTITY_MODELS.get(operation.entity_type)
        if model is None:
            return None
        result = await session.execute(
            select(model)
            .where(model.owner_id == owner_id, model.id == operation.entity_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def _load_revisions(
        self,
        session: AsyncSession,
        owner_id: str,
        entity_type: EntityType,
        entity_id: str,
    ) -> dict[int, EntitySnapshot]:
        rows = (
            await session.execute(
                select(SyncEntityRevision).where(
                    SyncEntityRevision.owner_id == owner_id,
                    SyncEntityRevision.entity_type == entity_type.value,
                    SyncEntityRevision.entity_id == entity_id,
                )
            )
        ).scalars()
        return {row.revision: EntitySnapshot.model_validate(row.snapshot) for row in rows}

    async def _load_conflicts(
        self,
        session: AsyncSession,
        owner_id: str,
        entity_type: EntityType,
        entity_id: str,
    ) -> dict[UUID, ConflictRecord]:
        rows = (
            await session.execute(
                select(SyncConflict).where(
                    SyncConflict.owner_id == owner_id,
                    SyncConflict.entity_type == entity_type.value,
                    SyncConflict.entity_id == entity_id,
                )
            )
        ).scalars()
        return {row.conflict_id: ConflictRecord.model_validate(row.record) for row in rows}

    async def _persist_entity(
        self,
        session: AsyncSession,
        owner_id: str,
        row: Any | None,
        snapshot: EntitySnapshot,
    ) -> None:
        model = self._ENTITY_MODELS[snapshot.entity_type]
        now = utcnow()
        if snapshot.deleted:
            if row is None:
                raise RuntimeError("sync engine attempted to tombstone an absent entity")
            row.deleted_at = now
            row.revision = snapshot.revision
            row.updated_at = now
            return

        values = _domain_values(snapshot.entity_type, snapshot.data or {})
        if row is None:
            row = model(
                id=snapshot.entity_id,
                owner_id=owner_id,
                revision=snapshot.revision,
                deleted_at=None,
                created_at=now,
                updated_at=now,
                **values,
            )
            session.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
            row.revision = snapshot.revision
            row.deleted_at = None
            row.updated_at = now

    async def _record_revision(
        self, session: AsyncSession, owner_id: str, snapshot: EntitySnapshot
    ) -> None:
        existing = await session.get(
            SyncEntityRevision,
            (owner_id, snapshot.entity_type.value, snapshot.entity_id, snapshot.revision),
        )
        if existing is None:
            session.add(
                SyncEntityRevision(
                    owner_id=owner_id,
                    entity_type=snapshot.entity_type.value,
                    entity_id=snapshot.entity_id,
                    revision=snapshot.revision,
                    snapshot=snapshot.model_dump(mode="json"),
                )
            )

    async def _append_change(
        self, session: AsyncSession, owner_id: str, snapshot: EntitySnapshot
    ) -> None:
        event_id = uuid4()
        event = SyncChangeEvent(
            event_id=event_id,
            owner_id=owner_id,
            entity_type=snapshot.entity_type.value,
            entity_id=snapshot.entity_id,
            revision=snapshot.revision,
            snapshot=None if snapshot.deleted else snapshot.model_dump(mode="json"),
            tombstone=None,
        )
        session.add(event)
        await session.flush()
        if snapshot.deleted:
            tombstone = Tombstone(
                entity_type=snapshot.entity_type,
                entity_id=snapshot.entity_id,
                revision=snapshot.revision,
                sequence=event.sequence,
                deleted_at=utcnow(),
            )
            event.tombstone = tombstone.model_dump(mode="json")
            session.add(
                SyncTombstone(
                    owner_id=owner_id,
                    entity_type=snapshot.entity_type.value,
                    entity_id=snapshot.entity_id,
                    revision=snapshot.revision,
                    sequence=event.sequence,
                    event_id=event_id,
                    deleted_at=tombstone.deleted_at,
                )
            )
            await session.flush()

    async def _record_receipt(
        self,
        session: AsyncSession,
        owner_id: str,
        operation: SyncOperation,
        fingerprint: str,
        acknowledgement: Acknowledgement,
    ) -> None:
        session.add(
            SyncOperationReceipt(
                owner_id=owner_id,
                operation_id=operation.operation_id,
                idempotency_key=operation.idempotency_key,
                request_fingerprint=fingerprint,
                operation_envelope=operation.model_dump(mode="json"),
                acknowledgement=acknowledgement.model_dump(mode="json"),
            )
        )

    def _payload_error(self, operation: SyncOperation) -> str | None:
        if operation.payload is None:
            return None
        allowed = self._SYNC_FIELDS[operation.entity_type]
        extra = set(operation.payload) - allowed
        missing = allowed - set(operation.payload)
        required = self._REQUIRED_FIELDS[operation.entity_type]
        if extra:
            return f"payload contains unsupported fields: {', '.join(sorted(extra))}"
        if missing:
            missing_fields = ", ".join(sorted(missing))
            return (
                "payload must contain the complete entity document; "
                f"missing: {missing_fields}"
            )
        if operation.operation_type.value in {"create", "update", "resolve_conflict"}:
            value = operation.payload.get(required)
            if not isinstance(value, str) or not value.strip():
                return f"payload requires non-empty {required}"
        try:
            _domain_values(operation.entity_type, operation.payload)
        except (TypeError, ValueError) as exc:
            return str(exc)
        return None

    async def _reference_error(
        self, session: AsyncSession, owner_id: str, operation: SyncOperation
    ) -> str | None:
        if operation.entity_type != EntityType.TASK or operation.payload is None:
            return None
        for field, model in (
            ("project_id", db_models.Project),
            ("capture_id", db_models.Capture),
        ):
            reference_id = operation.payload.get(field)
            if reference_id is None:
                continue
            if not isinstance(reference_id, str) or not _is_ulid(reference_id):
                return f"{field} must be a ULID or null"
            exists = await session.scalar(
                select(model.id).where(
                    model.id == reference_id,
                    model.owner_id == owner_id,
                    model.deleted_at.is_(None),
                )
            )
            if exists is None:
                return f"{field} must reference an active record owned by the same user"
        return None

    @staticmethod
    def _permanent(
        operation: SyncOperation, category: ErrorCategory, message: str
    ) -> Acknowledgement:
        return Acknowledgement(
            operation_id=operation.operation_id,
            status=AckStatus.PERMANENT,
            error_category=category,
            message=message,
        )

    @staticmethod
    def _idempotency_mismatch(operation: SyncOperation) -> Acknowledgement:
        return PostgresSyncStore._permanent(
            operation,
            ErrorCategory.IDEMPOTENCY_MISMATCH,
            "operation ID or idempotency key was already used with a different request",
        )


class _ReceiptMismatch:
    """Sentinel recognized by the pre-engine idempotency check."""


def _snapshot_from_row(
    entity_type: EntityType, entity_id: str, row: Any | None
) -> EntitySnapshot | None:
    if row is None:
        return None
    if row.deleted_at is not None:
        return EntitySnapshot(
            entity_type=entity_type,
            entity_id=entity_id,
            revision=row.revision,
            data=None,
            deleted=True,
        )
    fields = PostgresSyncStore._SYNC_FIELDS[entity_type]
    data = {key: getattr(row, key) for key in fields}
    return EntitySnapshot(
        entity_type=entity_type,
        entity_id=entity_id,
        revision=row.revision,
        data=json.loads(json.dumps(data, default=_json_default)),
        deleted=False,
    )


def _domain_values(entity_type: EntityType, payload: dict[str, Any]) -> dict[str, Any]:
    if entity_type == EntityType.TASK:
        values = {
            "title": payload.get("title"),
            "description": payload.get("description"),
            "status": payload.get("status", "open"),
            "priority": payload.get("priority"),
            "due_date": _parse_datetime(payload.get("due_date")),
            "project_id": payload.get("project_id"),
            "capture_id": payload.get("capture_id"),
        }
        if len(values["title"] or "") > 512:
            raise ValueError("task title exceeds 512 characters")
        description = values["description"]
        if description is not None and len(description) > 5000:
            raise ValueError("task description exceeds 5000 characters")
        if not isinstance(values["status"], str) or len(values["status"]) > 32:
            raise ValueError("task status must be a string up to 32 characters")
        priority = values["priority"]
        if priority is not None and (type(priority) is not int or not 1 <= priority <= 5):
            raise ValueError("task priority must be an integer from 1 to 5")
        for field in ("project_id", "capture_id"):
            reference_id = values[field]
            if reference_id is not None and (
                not isinstance(reference_id, str) or not _is_ulid(reference_id)
            ):
                raise ValueError(f"{field} must be a ULID or null")
        return values
    if entity_type == EntityType.PROJECT:
        values = {
            "name": payload.get("name"),
            "description": payload.get("description"),
            "status": payload.get("status", "active"),
        }
        if len(values["name"] or "") > 255:
            raise ValueError("project name exceeds 255 characters")
        description = values["description"]
        if description is not None and len(description) > 5000:
            raise ValueError("project description exceeds 5000 characters")
        if not isinstance(values["status"], str) or len(values["status"]) > 32:
            raise ValueError("project status must be a string up to 32 characters")
        return values
    raise ValueError(f"unsupported sync entity type: {entity_type.value}")


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("due_date must be an ISO-8601 datetime") from exc
    else:
        raise ValueError("due_date must be an ISO-8601 datetime or null")
    if parsed is not None and (parsed.tzinfo is None or parsed.utcoffset() is None):
        raise ValueError("due_date must include a timezone")
    return parsed.astimezone(UTC) if parsed is not None else None


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    raise TypeError(f"cannot encode {type(value).__name__} as JSON")


def _is_ulid(value: str) -> bool:
    alphabet = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
    return len(value) == 26 and all(character in alphabet for character in value)


def _operation_fingerprint(operation: SyncOperation) -> str:
    """Canonical v1 request fingerprint, shared with the deterministic engine."""
    canonical = json.dumps(
        operation.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
