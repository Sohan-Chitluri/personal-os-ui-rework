"""Deterministic, storage-independent operation reconciliation engine.

Payloads for create, update, and conflict resolution are complete entity
documents. The engine never guesses how to merge domain fields: every mutation
uses an exact integer revision precondition, and unsafe changes become durable
conflicts through the adapter's atomic commit operation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid5

from pydantic import ValidationError

from personal_os.sync.contracts import (
    PROTOCOL_VERSION,
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

_CONFLICT_NAMESPACE = UUID("6b0d8635-197e-4b9b-a3db-332ec49c8eb3")
_SUPPORTED_OPERATIONS = frozenset(OperationType)


@dataclass(frozen=True, slots=True)
class OperationReceipt:
    """Previously committed request identity and its stable acknowledgement."""

    operation_id: UUID
    idempotency_key: str
    request_fingerprint: str
    acknowledgement: Acknowledgement


@dataclass(frozen=True, slots=True)
class CommitResult:
    """Result of an adapter's atomic compare-and-commit attempt.

    Exactly one of ``committed``, ``state_changed``, ``replayed_ack``, or
    ``idempotency_mismatch`` is expected to be set by a conforming adapter.
    """

    committed: bool = False
    state_changed: bool = False
    replayed_ack: Acknowledgement | None = None
    idempotency_mismatch: bool = False


class SyncAdapter(Protocol):
    """Repository seam required by :class:`SyncEngine`.

    ``commit`` must atomically verify the request identities, compare the
    current entity with ``expected_current`` when ``check_current`` is true,
    apply the new snapshot, persist the acknowledgement/conflict, and mark a
    resolved conflict when requested. ``expected_current=None`` means the
    entity must be absent. With ``check_current=False``, only an acknowledgement
    receipt is written (for terminal validation errors).
    """

    def lookup_operation(
        self, operation_id: UUID, idempotency_key: str
    ) -> OperationReceipt | None: ...

    def get_current(
        self, entity_type: EntityType, entity_id: str
    ) -> EntitySnapshot | None: ...

    def get_revision(
        self, entity_type: EntityType, entity_id: str, revision: int
    ) -> EntitySnapshot | None: ...

    def get_conflict(self, conflict_id: UUID) -> ConflictRecord | None: ...

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
    ) -> CommitResult: ...


class SyncEngine:
    """Apply one versioned operation at a time against an adapter.

    The engine has no wall-clock decisions and is deterministic for the same
    operation and adapter state. A bounded re-read loop handles concurrent
    writes without allowing a stale evaluation to overwrite newer state.
    """

    def __init__(
        self,
        adapter: SyncAdapter,
        *,
        supported_entity_types: frozenset[EntityType] | None = None,
        supported_operations: frozenset[OperationType] = _SUPPORTED_OPERATIONS,
        max_compare_retries: int = 8,
    ) -> None:
        if max_compare_retries < 1:
            raise ValueError("max_compare_retries must be at least 1")
        self._adapter = adapter
        self._supported_entity_types = (
            frozenset(EntityType)
            if supported_entity_types is None
            else supported_entity_types
        )
        self._supported_operations = supported_operations
        self._max_compare_retries = max_compare_retries

    def apply(self, operation: SyncOperation) -> Acknowledgement:
        """Apply an operation, replaying a stored result for duplicate delivery."""

        op, validation_error = self._validated_operation(operation)
        if validation_error is not None:
            # A malformed model may not satisfy the adapter's typed operation
            # boundary, so it is reported without attempting a repository write.
            return validation_error

        fingerprint = self._fingerprint(op)
        receipt = self._adapter.lookup_operation(op.operation_id, op.idempotency_key)
        if receipt is not None:
            if receipt.request_fingerprint != fingerprint:
                return self._permanent(
                    op,
                    ErrorCategory.IDEMPOTENCY_MISMATCH,
                    "operation ID or idempotency key was already used with a different request",
                )
            return receipt.acknowledgement

        if op.protocol_version != PROTOCOL_VERSION:
            return self._persist_terminal(
                op,
                fingerprint,
                self._failure(
                    op,
                    ErrorCategory.PROTOCOL_MISMATCH,
                    "unsupported sync protocol version",
                ),
            )
        if op.entity_type not in self._supported_entity_types:
            return self._persist_terminal(
                op,
                fingerprint,
                self._failure(
                    op,
                    ErrorCategory.UNSUPPORTED,
                    f"unsupported entity type: {op.entity_type}",
                ),
            )
        if op.operation_type not in self._supported_operations:
            return self._persist_terminal(
                op,
                fingerprint,
                self._failure(
                    op,
                    ErrorCategory.UNSUPPORTED,
                    f"unsupported operation type: {op.operation_type}",
                ),
            )

        for _ in range(self._max_compare_retries):
            result = self._evaluate(op, fingerprint)
            if result is not None:
                return result

        return Acknowledgement(
            operation_id=op.operation_id,
            status=AckStatus.RETRYABLE,
            error_category=ErrorCategory.TEMPORARY_SERVICE,
            message="entity changed repeatedly while applying operation; retry later",
        )

    def _evaluate(
        self, op: SyncOperation, fingerprint: str
    ) -> Acknowledgement | None:
        if op.operation_type == OperationType.RESOLVE_CONFLICT:
            return self._evaluate_resolution(op, fingerprint)

        current = self._adapter.get_current(op.entity_type, op.entity_id)
        if op.operation_type == OperationType.CREATE:
            if current is not None:
                return self._save_conflict(
                    op,
                    fingerprint,
                    expected_current=current,
                    base_snapshot=None,
                    remote_snapshot=current,
                    reason=ErrorCategory.INVALID_OPERATION,
                )
            assert op.payload is not None
            snapshot = EntitySnapshot(
                entity_type=op.entity_type,
                entity_id=op.entity_id,
                revision=1,
                data=op.payload,
                deleted=False,
            )
            return self._save_applied(
                op,
                fingerprint,
                expected_current=None,
                snapshot=snapshot,
            )

        if current is None:
            if op.depends_on:
                # A dependent update/delete may have arrived before its create.
                # Keep it retryable and do not store a terminal receipt so the
                # same operation can be retried after its prerequisite arrives.
                return Acknowledgement(
                    operation_id=op.operation_id,
                    status=AckStatus.RETRYABLE,
                    error_category=ErrorCategory.MISSING_BASE,
                    message=(
                        "entity is not present yet and the operation has an "
                        "undelivered prerequisite"
                    ),
                )
            return self._persist_terminal(
                op,
                fingerprint,
                self._failure(
                    op,
                    ErrorCategory.MISSING_ENTITY,
                    "operation targets an entity that is not present",
                ),
            )

        assert op.base_revision is not None
        if current.deleted and op.base_revision >= current.revision:
            # A tombstone cannot advance to the requested future revision.
            # Treat it as a durable conflict instead of retrying forever.
            return self._save_conflict(
                op,
                fingerprint,
                expected_current=current,
                base_snapshot=(
                    current if op.base_revision == current.revision else None
                ),
                remote_snapshot=current,
                reason=ErrorCategory.MISSING_ENTITY,
            )
        if op.base_revision > current.revision:
            # This can be an operation delivered before its dependency. Do not
            # store a terminal receipt; a retry must be evaluated against the
            # state after the missing earlier operation arrives.
            return Acknowledgement(
                operation_id=op.operation_id,
                status=AckStatus.RETRYABLE,
                error_category=ErrorCategory.MISSING_BASE,
                message="requested base revision is newer than the current entity revision",
            )

        if op.base_revision < current.revision:
            base_snapshot = self._adapter.get_revision(
                op.entity_type, op.entity_id, op.base_revision
            )
            reason = (
                ErrorCategory.STALE_REVISION
                if base_snapshot is not None
                else ErrorCategory.EXPIRED_BASE
            )
            return self._save_conflict(
                op,
                fingerprint,
                expected_current=current,
                base_snapshot=base_snapshot,
                remote_snapshot=current,
                reason=reason,
            )

        if op.operation_type == OperationType.DELETE:
            next_snapshot = EntitySnapshot(
                entity_type=op.entity_type,
                entity_id=op.entity_id,
                revision=current.revision + 1,
                data=None,
                deleted=True,
            )
        else:
            assert op.payload is not None
            next_snapshot = EntitySnapshot(
                entity_type=op.entity_type,
                entity_id=op.entity_id,
                revision=current.revision + 1,
                data=op.payload,
                deleted=False,
            )
        return self._save_applied(
            op,
            fingerprint,
            expected_current=current,
            snapshot=next_snapshot,
        )

    def _evaluate_resolution(
        self, op: SyncOperation, fingerprint: str
    ) -> Acknowledgement | None:
        assert op.resolves_conflict_id is not None
        original_conflict = self._adapter.get_conflict(op.resolves_conflict_id)
        if original_conflict is None:
            return self._persist_terminal(
                op,
                fingerprint,
                self._failure(
                    op,
                    ErrorCategory.INVALID_OPERATION,
                    "conflict resolution references an unknown conflict",
                ),
            )
        if original_conflict.status == ConflictStatus.RESOLVED:
            return self._persist_terminal(
                op,
                fingerprint,
                self._failure(
                    op,
                    ErrorCategory.INVALID_OPERATION,
                    "conflict has already been resolved by another operation",
                ),
            )
        if (
            original_conflict.operation.entity_type != op.entity_type
            or original_conflict.operation.entity_id != op.entity_id
        ):
            return self._persist_terminal(
                op,
                fingerprint,
                self._failure(
                    op,
                    ErrorCategory.INVALID_OPERATION,
                    "resolution entity does not match the referenced conflict",
                ),
            )

        current = self._adapter.get_current(op.entity_type, op.entity_id)
        if current is None:
            return self._persist_terminal(
                op,
                fingerprint,
                self._failure(
                    op,
                    ErrorCategory.MISSING_ENTITY,
                    "cannot resolve a conflict after the entity disappeared",
                ),
            )

        assert op.base_revision is not None
        if current.deleted and op.base_revision >= current.revision:
            return self._save_conflict(
                op,
                fingerprint,
                expected_current=current,
                base_snapshot=(
                    current if op.base_revision == current.revision else None
                ),
                remote_snapshot=current,
                reason=ErrorCategory.MISSING_ENTITY,
            )
        if op.base_revision > current.revision:
            return Acknowledgement(
                operation_id=op.operation_id,
                status=AckStatus.RETRYABLE,
                error_category=ErrorCategory.MISSING_BASE,
                message="requested resolution base is newer than the current entity revision",
            )
        if op.base_revision < current.revision:
            base_snapshot = self._adapter.get_revision(
                op.entity_type, op.entity_id, op.base_revision
            )
            reason = (
                ErrorCategory.STALE_REVISION
                if base_snapshot is not None
                else ErrorCategory.EXPIRED_BASE
            )
            return self._save_conflict(
                op,
                fingerprint,
                expected_current=current,
                base_snapshot=base_snapshot,
                remote_snapshot=current,
                reason=reason,
            )
        assert op.payload is not None
        next_snapshot = EntitySnapshot(
            entity_type=op.entity_type,
            entity_id=op.entity_id,
            revision=current.revision + 1,
            data=op.payload,
            deleted=False,
        )
        acknowledgement = Acknowledgement(
            operation_id=op.operation_id,
            status=AckStatus.APPLIED,
            revision=next_snapshot.revision,
        )
        return self._commit(
            op,
            fingerprint,
            expected_current=current,
            new_snapshot=next_snapshot,
            acknowledgement=acknowledgement,
            resolve_conflict_id=original_conflict.conflict_id,
        )

    def _save_applied(
        self,
        op: SyncOperation,
        fingerprint: str,
        *,
        expected_current: EntitySnapshot | None,
        snapshot: EntitySnapshot,
    ) -> Acknowledgement | None:
        acknowledgement = Acknowledgement(
            operation_id=op.operation_id,
            status=AckStatus.APPLIED,
            revision=snapshot.revision,
        )
        return self._commit(
            op,
            fingerprint,
            expected_current=expected_current,
            new_snapshot=snapshot,
            acknowledgement=acknowledgement,
        )

    def _save_conflict(
        self,
        op: SyncOperation,
        fingerprint: str,
        *,
        expected_current: EntitySnapshot,
        base_snapshot: EntitySnapshot | None,
        remote_snapshot: EntitySnapshot,
        reason: ErrorCategory,
    ) -> Acknowledgement | None:
        conflict_id = self._conflict_id(op, fingerprint, remote_snapshot, reason)
        conflict = ConflictRecord(
            conflict_id=conflict_id,
            operation=op,
            base_revision=op.base_revision,
            base_snapshot=base_snapshot,
            local_payload=op.payload,
            remote_snapshot=remote_snapshot,
            reason=reason,
            status=ConflictStatus.UNRESOLVED,
            created_at=op.created_at,
        )
        acknowledgement = Acknowledgement(
            operation_id=op.operation_id,
            status=AckStatus.CONFLICTED,
            revision=remote_snapshot.revision,
            conflict_id=conflict_id,
            error_category=reason,
            message=(
                "operation conflicts with the current cloud revision; "
                "both versions were preserved"
            ),
        )
        return self._commit(
            op,
            fingerprint,
            expected_current=expected_current,
            new_snapshot=None,
            acknowledgement=acknowledgement,
            conflict=conflict,
        )

    def _persist_terminal(
        self,
        op: SyncOperation,
        fingerprint: str,
        acknowledgement: Acknowledgement,
    ) -> Acknowledgement:
        result = self._adapter.commit(
            operation=op,
            request_fingerprint=fingerprint,
            expected_current=None,
            check_current=False,
            new_snapshot=None,
            acknowledgement=acknowledgement,
        )
        if result.replayed_ack is not None:
            return result.replayed_ack
        if result.idempotency_mismatch:
            return self._permanent(
                op,
                ErrorCategory.IDEMPOTENCY_MISMATCH,
                "operation ID or idempotency key was already used with a different request",
            )
        if result.state_changed:
            raise RuntimeError("adapter changed entity state during an unchecked receipt commit")
        if not result.committed:
            raise RuntimeError("adapter did not commit terminal acknowledgement")
        return acknowledgement

    def _commit(
        self,
        op: SyncOperation,
        fingerprint: str,
        *,
        expected_current: EntitySnapshot | None,
        new_snapshot: EntitySnapshot | None,
        acknowledgement: Acknowledgement,
        conflict: ConflictRecord | None = None,
        resolve_conflict_id: UUID | None = None,
    ) -> Acknowledgement | None:
        result = self._adapter.commit(
            operation=op,
            request_fingerprint=fingerprint,
            expected_current=expected_current,
            check_current=True,
            new_snapshot=new_snapshot,
            acknowledgement=acknowledgement,
            conflict=conflict,
            resolve_conflict_id=resolve_conflict_id,
        )
        if result.replayed_ack is not None:
            return result.replayed_ack
        if result.idempotency_mismatch:
            return self._permanent(
                op,
                ErrorCategory.IDEMPOTENCY_MISMATCH,
                "operation ID or idempotency key was already used with a different request",
            )
        if result.state_changed:
            return None
        if not result.committed:
            raise RuntimeError("adapter returned an incomplete commit result")
        return acknowledgement

    @staticmethod
    def _failure(
        op: SyncOperation, category: ErrorCategory, message: str
    ) -> Acknowledgement:
        return Acknowledgement(
            operation_id=op.operation_id,
            status=AckStatus.PERMANENT,
            error_category=category,
            message=message,
        )

    @staticmethod
    def _permanent(
        op: SyncOperation, category: ErrorCategory, message: str
    ) -> Acknowledgement:
        return SyncEngine._failure(op, category, message)

    @staticmethod
    def _fingerprint(op: SyncOperation) -> str:
        canonical = json.dumps(
            op.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _conflict_id(
        op: SyncOperation,
        fingerprint: str,
        remote_snapshot: EntitySnapshot,
        reason: ErrorCategory,
    ) -> UUID:
        name = ":".join(
            (
                str(op.operation_id),
                fingerprint,
                str(remote_snapshot.revision),
                reason.value,
            )
        )
        return uuid5(_CONFLICT_NAMESPACE, name)

    @staticmethod
    def _validated_operation(
        operation: SyncOperation,
    ) -> tuple[SyncOperation, Acknowledgement | None]:
        try:
            validated = SyncOperation.model_validate(
                operation.model_dump(mode="python", warnings=False)
            )
            return validated, None
        except ValidationError as exc:
            raw = operation.model_dump(mode="python", warnings=False)
            operation_id = raw.get("operation_id")
            if not isinstance(operation_id, UUID):
                raise ValueError("malformed sync operation has no usable operation ID") from exc
            raw_type = raw.get("operation_type")
            known_types = {item.value for item in OperationType}
            raw_version = raw.get("protocol_version")
            if raw_version != PROTOCOL_VERSION:
                category = ErrorCategory.PROTOCOL_MISMATCH
                message = "unsupported sync protocol version"
            elif isinstance(raw_type, str) and raw_type not in known_types:
                category = ErrorCategory.UNSUPPORTED
                message = f"unsupported operation type: {raw_type}"
            else:
                category = ErrorCategory.MALFORMED
                message = "operation does not satisfy the protocol v1 schema"
            return operation, Acknowledgement(
                operation_id=operation_id,
                status=AckStatus.PERMANENT,
                error_category=category,
                message=message,
            )
