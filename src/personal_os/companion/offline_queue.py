"""Durable SQLite operation queue for the local companion.

This module deliberately owns its SQLite storage. It is separate from both the
cloud database and the browser's IndexedDB adapter. An expired in-flight lease
is recovered as retryable and always keeps the same operation and idempotency
identifiers: a timeout or process crash is an unknown server outcome, not proof
that the server rejected the request.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

from pydantic import ValidationError

from personal_os.sync.contracts import (
    Acknowledgement,
    AckStatus,
    ErrorCategory,
    SyncOperation,
)


class QueueStatus(StrEnum):
    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    ACKNOWLEDGED = "acknowledged"
    RETRYABLE = "retryable"
    FAILED = "failed"
    CONFLICTED = "conflicted"
    CANCELLED = "cancelled"


class QueueError(Exception):
    """Base error for queue validation, state, and persistence failures."""


class QueueStorageError(QueueError):
    """The durable store could not complete an operation."""


class QueueValidationError(QueueError):
    """An operation or acknowledgement did not match the shared contract."""


class QueueIdempotencyError(QueueError):
    """An operation identifier was reused with a different operation body."""


class QueueStateError(QueueError):
    """A requested lifecycle transition is not safe or valid."""


@dataclass(frozen=True, slots=True)
class QueueEntry:
    operation_id: str
    idempotency_key: str
    operation: SyncOperation | None
    status: QueueStatus
    attempts: int
    next_attempt_at: datetime | None
    in_flight_until: datetime | None
    acknowledgement: Acknowledgement | None
    error_category: ErrorCategory | None
    last_error: str | None
    enqueued_at: datetime
    updated_at: datetime
    malformed: bool = False


@dataclass(frozen=True, slots=True)
class QueueDiagnostics:
    total: int
    by_status: dict[QueueStatus, int]
    malformed: int
    oldest_unresolved_at: datetime | None
    recent_errors: tuple[tuple[str, str], ...]


Jitter = Callable[[float], float]


class OperationQueue(Protocol):
    """Storage-neutral interface implemented by a local durable queue."""

    def enqueue(self, operation: SyncOperation) -> QueueEntry: ...

    def claim_batch(
        self, *, now: datetime | None = None, limit: int | None = None
    ) -> tuple[QueueEntry, ...]: ...

    def acknowledge(
        self, acknowledgement: Acknowledgement, *, now: datetime | None = None
    ) -> QueueEntry: ...

    def mark_retryable(
        self,
        operation_id: UUID | str,
        *,
        category: ErrorCategory,
        message: str,
        now: datetime | None = None,
        retry_after_seconds: float | None = None,
    ) -> QueueEntry: ...

    def mark_failed(
        self,
        operation_id: UUID | str,
        *,
        category: ErrorCategory,
        message: str,
        now: datetime | None = None,
    ) -> QueueEntry: ...

    def cancel(
        self, operation_id: UUID | str, *, now: datetime | None = None
    ) -> QueueEntry: ...

    def inspect(
        self, *, statuses: set[QueueStatus] | None = None, limit: int | None = None
    ) -> tuple[QueueEntry, ...]: ...

    def diagnostics(self, *, recent_error_limit: int = 10) -> QueueDiagnostics: ...


def _utc(value: datetime | None) -> datetime:
    result = value or datetime.now(UTC)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("queue timestamps must be timezone-aware")
    return result.astimezone(UTC)


def _encode_model(value: SyncOperation | Acknowledgement) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _decode_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored timestamp has no timezone")
    return parsed.astimezone(UTC)


class SQLiteOperationQueue:
    """SQLite-backed queue used by the local companion.

    Each queue instance owns one connection. Transactions use ``BEGIN
    IMMEDIATE`` so separate queue instances/processes cannot claim the same
    operation concurrently. ``jitter_fn`` receives the exponential delay cap
    in seconds and must return a delay; results are clamped to the cap.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_batch_size: int = 50,
        max_attempts: int = 8,
        in_flight_timeout_seconds: float = 120.0,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 300.0,
        jitter_fn: Jitter | None = None,
    ) -> None:
        if max_batch_size < 1:
            raise ValueError("max_batch_size must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if not math.isfinite(in_flight_timeout_seconds) or in_flight_timeout_seconds < 0:
            raise ValueError("in_flight_timeout_seconds must be finite and nonnegative")
        if (
            not math.isfinite(base_backoff_seconds)
            or not math.isfinite(max_backoff_seconds)
            or base_backoff_seconds < 0
            or max_backoff_seconds < 0
        ):
            raise ValueError("backoff values must be finite and nonnegative")
        self.max_batch_size = max_batch_size
        self.max_attempts = max_attempts
        self.in_flight_timeout_seconds = in_flight_timeout_seconds
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.jitter_fn = jitter_fn or (lambda cap: random.uniform(0.0, cap))
        self._lock = threading.RLock()
        try:
            if str(path) != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(
                str(path), timeout=5.0, isolation_level=None, check_same_thread=False
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA busy_timeout = 5000")
            if str(path) != ":memory:":
                self._connection.execute("PRAGMA journal_mode = WAL")
            self._create_schema()
        except (OSError, sqlite3.Error) as exc:
            raise QueueStorageError(f"cannot initialize offline queue: {exc}") from exc

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS offline_operations (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL UNIQUE,
                idempotency_key TEXT NOT NULL UNIQUE,
                operation_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT,
                in_flight_until TEXT,
                acknowledgement_json TEXT,
                error_category TEXT,
                last_error TEXT,
                enqueued_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS offline_operations_ready
                ON offline_operations(status, next_attempt_at, sequence);
            """
        )
        columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(offline_operations)")
        }
        if "in_flight_until" not in columns:
            self._connection.execute(
                "ALTER TABLE offline_operations ADD COLUMN in_flight_until TEXT"
            )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                yield self._connection
                self._connection.execute("COMMIT")
            except QueueError:
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise
            except sqlite3.Error as exc:
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise QueueStorageError(f"offline queue storage failure: {exc}") from exc
            except Exception:
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                raise

    def _recover_expired_in_flight(
        self, connection: sqlite3.Connection, now: datetime
    ) -> None:
        now_text = now.isoformat()
        expiry_text = now_text
        try:
            connection.execute(
                """UPDATE offline_operations
                   SET status = ?, next_attempt_at = ?, in_flight_until = NULL,
                       error_category = ?, last_error = ?, updated_at = ?
                   WHERE status = ? AND (in_flight_until IS NULL OR in_flight_until <= ?)""",
                (
                    QueueStatus.RETRYABLE.value,
                    now_text,
                    ErrorCategory.TRANSPORT.value,
                    "in-flight lease expired; server outcome is unknown and the same idempotency "
                    "key will be retried",
                    now_text,
                    QueueStatus.IN_FLIGHT.value,
                    expiry_text,
                ),
            )
        except sqlite3.Error as exc:
            raise QueueStorageError(f"cannot recover in-flight queue entries: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            try:
                self._connection.close()
            except sqlite3.Error as exc:
                raise QueueStorageError(f"cannot close offline queue: {exc}") from exc

    def __enter__(self) -> SQLiteOperationQueue:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def enqueue(self, operation: SyncOperation) -> QueueEntry:
        try:
            validated = SyncOperation.model_validate(operation)
        except (ValidationError, TypeError, ValueError) as exc:
            raise QueueValidationError(f"invalid sync operation: {exc}") from exc
        encoded = _encode_model(validated)
        now = datetime.now(UTC).isoformat()
        with self._transaction() as connection:
            existing = connection.execute(
                """SELECT * FROM offline_operations
                   WHERE operation_id = ? OR idempotency_key = ?""",
                (str(validated.operation_id), validated.idempotency_key),
            ).fetchone()
            if existing is not None:
                if (
                    existing["operation_id"] != str(validated.operation_id)
                    or existing["idempotency_key"] != validated.idempotency_key
                    or existing["operation_json"] != encoded
                ):
                    raise QueueIdempotencyError(
                        "operation ID or idempotency key already belongs to a different body"
                    )
                return self._entry(existing)
            connection.execute(
                """INSERT INTO offline_operations (
                       operation_id, idempotency_key, operation_json, status,
                       attempts, next_attempt_at, acknowledgement_json,
                       error_category, last_error, enqueued_at, updated_at
                   ) VALUES (?, ?, ?, ?, 0, NULL, NULL, NULL, NULL, ?, ?)""",
                (
                    str(validated.operation_id),
                    validated.idempotency_key,
                    encoded,
                    QueueStatus.PENDING.value,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM offline_operations WHERE operation_id = ?",
                (str(validated.operation_id),),
            ).fetchone()
            assert row is not None
            return self._entry(row)

    def claim_batch(
        self,
        *,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[QueueEntry, ...]:
        current = _utc(now)
        current_text = current.isoformat()
        bounded_limit = min(self.max_batch_size if limit is None else limit, self.max_batch_size)
        if bounded_limit < 1:
            raise ValueError("limit must be positive")
        claimed: list[QueueEntry] = []
        with self._transaction() as connection:
            self._recover_expired_in_flight(connection, current)
            rows = connection.execute(
                """SELECT * FROM offline_operations
                   WHERE status IN (?, ?)
                     AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                   ORDER BY sequence""",
                (QueueStatus.PENDING.value, QueueStatus.RETRYABLE.value, current_text),
            ).fetchall()
            statuses = {
                row["operation_id"]: row["status"]
                for row in connection.execute(
                    "SELECT operation_id, status FROM offline_operations"
                ).fetchall()
            }
            for row in rows:
                if len(claimed) >= bounded_limit:
                    break
                try:
                    operation = SyncOperation.model_validate_json(row["operation_json"])
                except (ValidationError, ValueError, TypeError) as exc:
                    connection.execute(
                        """UPDATE offline_operations SET status = ?, error_category = ?,
                               last_error = ?, next_attempt_at = NULL, updated_at = ?
                           WHERE operation_id = ?""",
                        (
                            QueueStatus.FAILED.value,
                            ErrorCategory.MALFORMED.value,
                            f"stored operation is malformed: {exc}",
                            current_text,
                            row["operation_id"],
                        ),
                    )
                    continue

                dependency_states = [statuses.get(str(dep)) for dep in operation.depends_on]
                if any(
                    state in {
                        QueueStatus.FAILED.value,
                        QueueStatus.CONFLICTED.value,
                        QueueStatus.CANCELLED.value,
                    }
                    for state in dependency_states
                ):
                    connection.execute(
                        """UPDATE offline_operations SET status = ?, error_category = ?,
                               last_error = ?, next_attempt_at = NULL, updated_at = ?
                           WHERE operation_id = ?""",
                        (
                            QueueStatus.FAILED.value,
                            ErrorCategory.DEPENDENCY_FAILED.value,
                            "a required operation failed, conflicted, or was cancelled",
                            current_text,
                            row["operation_id"],
                        ),
                    )
                    continue
                missing_dependencies = [
                    str(dep)
                    for dep in operation.depends_on
                    if statuses.get(str(dep)) is None
                ]
                if any(state != QueueStatus.ACKNOWLEDGED.value for state in dependency_states):
                    if dependency_states:
                        if missing_dependencies:
                            reason = (
                                "waiting for missing dependency "
                                f"{', '.join(missing_dependencies)}"
                            )
                        else:
                            waiting_ids = [
                                str(dep)
                                for dep, state in zip(
                                    operation.depends_on, dependency_states, strict=True
                                )
                                if state != QueueStatus.ACKNOWLEDGED.value
                            ]
                            reason = f"waiting for prerequisite operation {', '.join(waiting_ids)}"
                        connection.execute(
                            """UPDATE offline_operations SET error_category = ?,
                                   last_error = ?, updated_at = ?
                               WHERE operation_id = ?""",
                            (
                                ErrorCategory.DEPENDENCY_PENDING.value,
                                reason,
                                current_text,
                                row["operation_id"],
                            ),
                        )
                    continue

                if int(row["attempts"]) >= self.max_attempts:
                    connection.execute(
                        """UPDATE offline_operations SET status = ?, error_category = ?,
                               last_error = ?, next_attempt_at = NULL, updated_at = ?
                           WHERE operation_id = ?""",
                        (
                            QueueStatus.FAILED.value,
                            ErrorCategory.TEMPORARY_SERVICE.value,
                            "retry attempts exhausted; operation retained for inspection",
                            current_text,
                            row["operation_id"],
                        ),
                    )
                    continue

                connection.execute(
                    """UPDATE offline_operations SET status = ?, attempts = attempts + 1,
                           next_attempt_at = NULL, in_flight_until = ?,
                           error_category = CASE WHEN error_category = ? THEN NULL
                                                 ELSE error_category END,
                           last_error = CASE WHEN error_category IS NULL
                                                  OR error_category = ? THEN NULL
                                             ELSE last_error END,
                           updated_at = ?
                       WHERE operation_id = ?""",
                    (
                        QueueStatus.IN_FLIGHT.value,
                        (current + timedelta(seconds=self.in_flight_timeout_seconds)).isoformat(),
                        ErrorCategory.DEPENDENCY_PENDING.value,
                        ErrorCategory.DEPENDENCY_PENDING.value,
                        current_text,
                        row["operation_id"],
                    ),
                )
                claimed_row = connection.execute(
                    "SELECT * FROM offline_operations WHERE operation_id = ?",
                    (row["operation_id"],),
                ).fetchone()
                assert claimed_row is not None
                claimed.append(self._entry(claimed_row))
                statuses[row["operation_id"]] = QueueStatus.IN_FLIGHT.value
        return tuple(claimed)

    def acknowledge(
        self, acknowledgement: Acknowledgement, *, now: datetime | None = None
    ) -> QueueEntry:
        try:
            ack = Acknowledgement.model_validate(acknowledgement)
        except (ValidationError, TypeError, ValueError) as exc:
            raise QueueValidationError(f"invalid acknowledgement: {exc}") from exc
        current_text = _utc(now).isoformat()
        ack_json = _encode_model(ack)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM offline_operations WHERE operation_id = ?",
                (str(ack.operation_id),),
            ).fetchone()
            if row is None:
                raise QueueStateError(
                    f"acknowledgement references unknown operation {ack.operation_id}"
                )
            if row["acknowledgement_json"] is not None:
                try:
                    existing_ack = Acknowledgement.model_validate_json(
                        row["acknowledgement_json"]
                    )
                except ValidationError as exc:
                    raise QueueStateError("stored acknowledgement is malformed") from exc
                if row["acknowledgement_json"] == ack_json:
                    return self._entry(row)
                if (
                    existing_ack.status in (AckStatus.APPLIED, AckStatus.DUPLICATE)
                    and ack.status in (AckStatus.APPLIED, AckStatus.DUPLICATE)
                    and existing_ack.revision == ack.revision
                ):
                    return self._entry(row)
                if existing_ack.status != AckStatus.RETRYABLE:
                    raise QueueStateError(
                        "operation already has a different durable acknowledgement"
                    )
            if row["status"] == QueueStatus.CANCELLED.value:
                raise QueueStateError("a cancelled operation cannot be acknowledged")

            if ack.status in (AckStatus.APPLIED, AckStatus.DUPLICATE):
                status = QueueStatus.ACKNOWLEDGED
                next_attempt_at = None
                category = None
                message = None
            elif ack.status == AckStatus.CONFLICTED:
                status = QueueStatus.CONFLICTED
                next_attempt_at = None
                category = None
                message = ack.message or "server reported a conflict"
            elif ack.status == AckStatus.PERMANENT:
                status = QueueStatus.FAILED
                next_attempt_at = None
                category = ack.error_category
                message = ack.message or "server permanently rejected the operation"
            else:
                category = ack.error_category or ErrorCategory.TEMPORARY_SERVICE
                message = ack.message or "server reported a retryable failure"
                if int(row["attempts"]) >= self.max_attempts:
                    status = QueueStatus.FAILED
                    next_attempt_at = None
                    message = f"retry attempts exhausted: {message}"
                else:
                    status = QueueStatus.RETRYABLE
                    delay = self._retry_delay(int(row["attempts"]), ack.retry_after_seconds)
                    next_attempt_at = (_utc(now) + timedelta(seconds=delay)).isoformat()

            connection.execute(
                """UPDATE offline_operations SET status = ?, acknowledgement_json = ?,
                       next_attempt_at = ?, in_flight_until = ?, error_category = ?,
                       last_error = ?, updated_at = ?
                   WHERE operation_id = ?""",
                (
                    status.value,
                    ack_json,
                    next_attempt_at,
                    None,
                    category.value if category else None,
                    message,
                    current_text,
                    str(ack.operation_id),
                ),
            )
            updated = connection.execute(
                "SELECT * FROM offline_operations WHERE operation_id = ?",
                (str(ack.operation_id),),
            ).fetchone()
            assert updated is not None
            return self._entry(updated)

    def mark_retryable(
        self,
        operation_id: UUID | str,
        *,
        category: ErrorCategory = ErrorCategory.TRANSPORT,
        message: str,
        now: datetime | None = None,
        retry_after_seconds: float | None = None,
    ) -> QueueEntry:
        current = _utc(now)
        with self._transaction() as connection:
            row = self._get_row(connection, operation_id)
            if row["status"] != QueueStatus.IN_FLIGHT.value:
                raise QueueStateError("only an in-flight operation can be marked retryable")
            attempts = int(row["attempts"])
            if attempts >= self.max_attempts:
                status = QueueStatus.FAILED
                due = None
                final_message = f"retry attempts exhausted: {message}"
            else:
                status = QueueStatus.RETRYABLE
                due = (current + timedelta(
                    seconds=self._retry_delay(attempts, retry_after_seconds)
                )).isoformat()
                final_message = message
            connection.execute(
                """UPDATE offline_operations SET status = ?, next_attempt_at = ?,
                       in_flight_until = NULL,
                       error_category = ?, last_error = ?, updated_at = ?
                   WHERE operation_id = ?""",
                (
                    status.value,
                    due,
                    category.value,
                    final_message,
                    current.isoformat(),
                    str(operation_id),
                ),
            )
            updated = self._get_row(connection, operation_id)
            return self._entry(updated)

    def mark_failed(
        self,
        operation_id: UUID | str,
        *,
        category: ErrorCategory,
        message: str,
        now: datetime | None = None,
    ) -> QueueEntry:
        current_text = _utc(now).isoformat()
        with self._transaction() as connection:
            row = self._get_row(connection, operation_id)
            if row["status"] not in (
                QueueStatus.IN_FLIGHT.value,
                QueueStatus.PENDING.value,
                QueueStatus.RETRYABLE.value,
            ):
                raise QueueStateError(
                    f"cannot permanently fail an operation in {row['status']} state"
                )
            connection.execute(
                """UPDATE offline_operations SET status = ?, next_attempt_at = NULL,
                       in_flight_until = NULL,
                       error_category = ?, last_error = ?, updated_at = ? WHERE operation_id = ?""",
                (
                    QueueStatus.FAILED.value,
                    category.value,
                    message,
                    current_text,
                    str(operation_id),
                ),
            )
            return self._entry(self._get_row(connection, operation_id))

    def cancel(self, operation_id: UUID | str, *, now: datetime | None = None) -> QueueEntry:
        current_text = _utc(now).isoformat()
        op_id = str(operation_id)
        with self._transaction() as connection:
            row = self._get_row(connection, operation_id)
            if int(row["attempts"]) != 0:
                raise QueueStateError(
                    "an operation that may have reached the server cannot be cancelled"
                )
            if row["status"] not in (QueueStatus.PENDING.value, QueueStatus.RETRYABLE.value):
                raise QueueStateError("only pending or retryable operations can be cancelled")
            all_rows = connection.execute(
                "SELECT operation_id, operation_json, status FROM offline_operations"
            ).fetchall()
            try:
                target_id = UUID(op_id)
            except ValueError as exc:
                raise QueueValidationError("operation ID must be a UUID") from exc
            for dependent in all_rows:
                if dependent["operation_id"] == op_id:
                    continue
                if dependent["status"] in (
                    QueueStatus.ACKNOWLEDGED.value,
                    QueueStatus.FAILED.value,
                    QueueStatus.CONFLICTED.value,
                    QueueStatus.CANCELLED.value,
                ):
                    continue
                try:
                    parsed = SyncOperation.model_validate_json(dependent["operation_json"])
                except (ValidationError, ValueError, TypeError):
                    continue
                if target_id in parsed.depends_on:
                    raise QueueStateError(
                        "operation has an unresolved dependent and cannot be cancelled"
                    )
            connection.execute(
                """UPDATE offline_operations SET status = ?, next_attempt_at = NULL,
                       in_flight_until = NULL,
                       error_category = NULL, last_error = 'cancelled before first send',
                       updated_at = ? WHERE operation_id = ?""",
                (QueueStatus.CANCELLED.value, current_text, op_id),
            )
            return self._entry(self._get_row(connection, operation_id))

    def inspect(
        self, *, statuses: set[QueueStatus] | None = None, limit: int | None = None
    ) -> tuple[QueueEntry, ...]:
        try:
            with self._lock:
                rows = self._connection.execute(
                    "SELECT * FROM offline_operations ORDER BY sequence"
                ).fetchall()
            entries = [self._entry(row) for row in rows]
            if statuses is not None:
                entries = [entry for entry in entries if entry.status in statuses]
            if limit is not None:
                if limit < 0:
                    raise ValueError("limit cannot be negative")
                entries = entries[:limit]
            return tuple(entries)
        except sqlite3.Error as exc:
            raise QueueStorageError(f"cannot inspect offline queue: {exc}") from exc

    def diagnostics(self, *, recent_error_limit: int = 10) -> QueueDiagnostics:
        entries = self.inspect()
        counts = dict.fromkeys(QueueStatus, 0)
        for entry in entries:
            counts[entry.status] += 1
        unresolved = [
            entry.enqueued_at
            for entry in entries
            if entry.status
            in (QueueStatus.PENDING, QueueStatus.IN_FLIGHT, QueueStatus.RETRYABLE)
        ]
        recent_errors = tuple(
            (entry.operation_id, entry.last_error or "")
            for entry in reversed(entries)
            if entry.last_error
        )[: max(0, recent_error_limit)]
        return QueueDiagnostics(
            total=len(entries),
            by_status=counts,
            malformed=sum(entry.malformed for entry in entries),
            oldest_unresolved_at=min(unresolved) if unresolved else None,
            recent_errors=recent_errors,
        )

    def _retry_delay(self, attempts: int, retry_after_seconds: float | None) -> float:
        exponent = max(0, attempts - 1)
        try:
            exponential = self.base_backoff_seconds * (2**exponent)
        except OverflowError:
            exponential = self.max_backoff_seconds
        cap = min(self.max_backoff_seconds, exponential)
        if retry_after_seconds is not None:
            cap = min(self.max_backoff_seconds, max(cap, max(0.0, retry_after_seconds)))
        try:
            jittered = float(self.jitter_fn(cap))
        except Exception as exc:
            raise QueueStateError(f"backoff jitter function failed: {exc}") from exc
        if not math.isfinite(jittered):
            raise QueueStateError("backoff jitter function returned a non-finite delay")
        jittered = min(cap, max(0.0, jittered))
        if retry_after_seconds is not None:
            jittered = max(jittered, min(self.max_backoff_seconds, retry_after_seconds))
        return float(min(self.max_backoff_seconds, jittered))

    @staticmethod
    def _get_row(connection: sqlite3.Connection, operation_id: UUID | str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM offline_operations WHERE operation_id = ?", (str(operation_id),)
        ).fetchone()
        if row is None:
            raise QueueStateError(f"unknown operation {operation_id}")
        return cast(sqlite3.Row, row)

    @staticmethod
    def _entry(row: sqlite3.Row) -> QueueEntry:
        malformed = False
        operation: SyncOperation | None
        ack: Acknowledgement | None
        try:
            operation = SyncOperation.model_validate_json(row["operation_json"])
        except (ValidationError, ValueError, TypeError):
            malformed = True
            operation = None
        try:
            ack = (
                Acknowledgement.model_validate_json(row["acknowledgement_json"])
                if row["acknowledgement_json"]
                else None
            )
        except (ValidationError, ValueError, TypeError):
            malformed = True
            ack = None
        try:
            status = QueueStatus(row["status"])
        except ValueError:
            status = QueueStatus.FAILED
            malformed = True
        try:
            category = ErrorCategory(row["error_category"]) if row["error_category"] else None
        except ValueError:
            category = ErrorCategory.MALFORMED
            malformed = True
        try:
            enqueued = _decode_datetime(row["enqueued_at"])
            updated = _decode_datetime(row["updated_at"])
            next_attempt = _decode_datetime(row["next_attempt_at"])
            in_flight_until = _decode_datetime(row["in_flight_until"])
        except ValueError:
            malformed = True
            enqueued = datetime.min.replace(tzinfo=UTC)
            updated = enqueued
            next_attempt = None
            in_flight_until = None
        assert enqueued is not None and updated is not None
        return QueueEntry(
            operation_id=row["operation_id"],
            idempotency_key=row["idempotency_key"],
            operation=operation,
            status=status,
            attempts=int(row["attempts"]),
            next_attempt_at=next_attempt,
            in_flight_until=in_flight_until,
            acknowledgement=ack,
            error_category=category,
            last_error=row["last_error"],
            enqueued_at=enqueued,
            updated_at=updated,
            malformed=malformed,
        )
