"""Tests for the companion's durable SQLite offline operation queue."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from personal_os.companion.offline_queue import (
    QueueIdempotencyError,
    QueueStateError,
    QueueStatus,
    QueueStorageError,
    QueueValidationError,
    SQLiteOperationQueue,
)
from personal_os.sync.contracts import (
    Acknowledgement,
    AckStatus,
    ErrorCategory,
    OperationType,
    SyncOperation,
)

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)


def operation(**overrides: object) -> SyncOperation:
    fields: dict[str, object] = {
        "operation_id": uuid4(),
        "idempotency_key": f"key-{uuid4()}",
        "entity_type": "task",
        "entity_id": "task-1",
        "operation_type": OperationType.UPDATE,
        "base_revision": 2,
        "payload": {"title": "complete local document"},
        "created_at": NOW,
        "depends_on": (),
        "resolves_conflict_id": None,
    }
    fields.update(overrides)
    return SyncOperation.model_validate(fields)


def applied(operation_value: SyncOperation) -> Acknowledgement:
    return Acknowledgement(
        operation_id=operation_value.operation_id,
        status=AckStatus.APPLIED,
        revision=3,
    )


def test_durable_enqueue_idempotency_and_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "queue.sqlite3"
    op = operation()
    with SQLiteOperationQueue(path) as queue:
        first = queue.enqueue(op)
        second = queue.enqueue(op)
        assert first.operation_id == second.operation_id
        assert first.status == second.status == QueueStatus.PENDING
        assert len(queue.inspect()) == 1

        with pytest.raises(QueueIdempotencyError):
            queue.enqueue(op.model_copy(update={"payload": {"title": "different"}}))

    reopened = SQLiteOperationQueue(path)
    assert reopened.inspect()[0].operation == op
    reopened.close()


def test_invalid_operation_is_rejected_before_it_is_queued(tmp_path: Path) -> None:
    with SQLiteOperationQueue(tmp_path / "queue.sqlite3") as queue:
        with pytest.raises(QueueValidationError):
            queue.enqueue({"operation_id": "not-an-operation"})  # type: ignore[arg-type]
        assert queue.inspect() == ()


def test_expired_in_flight_lease_recovers_same_idempotent_operation(tmp_path: Path) -> None:
    path = tmp_path / "queue.sqlite3"
    op = operation()
    first_queue = SQLiteOperationQueue(path, in_flight_timeout_seconds=0)
    first_queue.enqueue(op)
    first_claim = first_queue.claim_batch(now=NOW)[0]
    assert first_claim.status == QueueStatus.IN_FLIGHT
    assert first_claim.attempts == 1
    first_queue.close()

    restarted = SQLiteOperationQueue(path, in_flight_timeout_seconds=0)
    recovered = restarted.claim_batch(now=NOW + timedelta(seconds=1))[0]
    assert recovered.status == QueueStatus.IN_FLIGHT
    assert recovered.attempts == 2
    assert recovered.operation == op
    assert recovered.operation.operation_id == first_claim.operation.operation_id
    assert recovered.operation.idempotency_key == first_claim.operation.idempotency_key
    assert "outcome is unknown" in (recovered.last_error or "")
    restarted.close()


def test_retryable_timeout_uses_bounded_backoff_and_keeps_operation(tmp_path: Path) -> None:
    op = operation()
    with SQLiteOperationQueue(
        tmp_path / "queue.sqlite3",
        base_backoff_seconds=2,
        max_backoff_seconds=5,
        jitter_fn=lambda cap: cap,
    ) as queue:
        queue.enqueue(op)
        claimed = queue.claim_batch(now=NOW)[0]
        retry = queue.mark_retryable(
            claimed.operation_id,
            category=ErrorCategory.TRANSPORT,
            message="request timed out; server outcome is unknown",
            now=NOW,
        )
        assert retry.status == QueueStatus.RETRYABLE
        assert retry.next_attempt_at == NOW + timedelta(seconds=2)
        assert queue.claim_batch(now=NOW + timedelta(seconds=1)) == ()
        second = queue.claim_batch(now=NOW + timedelta(seconds=2))[0]
        assert second.operation == op
        assert second.operation_id == claimed.operation_id
        assert second.idempotency_key == claimed.idempotency_key
        assert second.attempts == 2


def test_acknowledgement_is_durable_and_duplicate_ack_is_safe(tmp_path: Path) -> None:
    with SQLiteOperationQueue(tmp_path / "queue.sqlite3") as queue:
        op = operation()
        queue.enqueue(op)
        queue.claim_batch(now=NOW)
        ack = applied(op)
        first = queue.acknowledge(ack, now=NOW)
        duplicate = queue.acknowledge(ack, now=NOW + timedelta(seconds=1))
        assert first.status == duplicate.status == QueueStatus.ACKNOWLEDGED
        assert duplicate.acknowledgement == ack
        equivalent_duplicate = Acknowledgement(
            operation_id=op.operation_id,
            status=AckStatus.DUPLICATE,
            revision=3,
        )
        assert queue.acknowledge(equivalent_duplicate).status == QueueStatus.ACKNOWLEDGED
        assert queue.claim_batch(now=NOW + timedelta(days=1)) == ()

        with pytest.raises(QueueStateError, match="different durable acknowledgement"):
            queue.acknowledge(
                Acknowledgement(
                    operation_id=op.operation_id,
                    status=AckStatus.DUPLICATE,
                    revision=4,
                )
            )


def test_retryable_server_ack_can_be_followed_by_success_after_retry(tmp_path: Path) -> None:
    op = operation()
    with SQLiteOperationQueue(
        tmp_path / "queue.sqlite3", base_backoff_seconds=1, jitter_fn=lambda cap: cap
    ) as queue:
        queue.enqueue(op)
        queue.claim_batch(now=NOW)
        retry_ack = Acknowledgement(
            operation_id=op.operation_id,
            status=AckStatus.RETRYABLE,
            error_category=ErrorCategory.TEMPORARY_SERVICE,
            retry_after_seconds=1,
        )
        assert queue.acknowledge(retry_ack, now=NOW).status == QueueStatus.RETRYABLE
        queue.claim_batch(now=NOW + timedelta(seconds=1))
        assert queue.acknowledge(applied(op), now=NOW + timedelta(seconds=1)).status == (
            QueueStatus.ACKNOWLEDGED
        )


def test_retry_exhaustion_retains_operation_for_inspection(tmp_path: Path) -> None:
    op = operation()
    with SQLiteOperationQueue(tmp_path / "queue.sqlite3", max_attempts=1) as queue:
        queue.enqueue(op)
        claimed = queue.claim_batch(now=NOW)[0]
        failed = queue.mark_retryable(
            claimed.operation_id,
            category=ErrorCategory.TRANSPORT,
            message="timed out",
            now=NOW,
        )
        assert failed.status == QueueStatus.FAILED
        assert failed.operation == op
        assert "exhausted" in (failed.last_error or "")
        assert queue.claim_batch(now=NOW + timedelta(days=1)) == ()
        assert queue.diagnostics().by_status[QueueStatus.FAILED] == 1


def test_dependency_order_and_failed_dependency_propagation(tmp_path: Path) -> None:
    parent = operation()
    child = operation(
        entity_id="task-2",
        depends_on=(parent.operation_id,),
    )
    with SQLiteOperationQueue(tmp_path / "queue.sqlite3") as queue:
        queue.enqueue(parent)
        queue.enqueue(child)
        assert [entry.operation_id for entry in queue.claim_batch(now=NOW)] == [
            str(parent.operation_id)
        ]
        queue.acknowledge(applied(parent), now=NOW)
        assert [entry.operation_id for entry in queue.claim_batch(now=NOW)] == [
            str(child.operation_id)
        ]

    with SQLiteOperationQueue(tmp_path / "failure.sqlite3") as queue:
        queue.enqueue(parent)
        queue.enqueue(child)
        queue.mark_failed(
            parent.operation_id,
            category=ErrorCategory.UNSUPPORTED,
            message="unsupported",
        )
        assert queue.claim_batch(now=NOW) == ()
        dependent = next(
            entry for entry in queue.inspect() if entry.operation_id == str(child.operation_id)
        )
        assert dependent.status == QueueStatus.FAILED
        assert dependent.error_category == ErrorCategory.DEPENDENCY_FAILED


def test_missing_dependency_waits_and_is_reported_until_prerequisite_arrives(
    tmp_path: Path,
) -> None:
    parent_id = uuid4()
    child = operation(depends_on=(parent_id,))
    parent = operation(
        operation_id=parent_id,
        idempotency_key="late-prerequisite",
        entity_id="task-parent",
        depends_on=(),
    )
    with SQLiteOperationQueue(tmp_path / "queue.sqlite3") as queue:
        queue.enqueue(child)
        assert queue.claim_batch(now=NOW) == ()
        assert "missing dependency" in (queue.inspect()[0].last_error or "")
        assert queue.inspect()[0].error_category == ErrorCategory.DEPENDENCY_PENDING
        queue.enqueue(parent)
        assert [entry.operation_id for entry in queue.claim_batch(now=NOW)] == [
            str(parent_id)
        ]
        queue.acknowledge(applied(parent), now=NOW)
        child_claim = queue.claim_batch(now=NOW)[0]
        assert child_claim.operation_id == str(child.operation_id)
        assert child_claim.last_error is None
        assert child_claim.error_category is None


def test_batch_size_is_bounded_and_zero_is_rejected(tmp_path: Path) -> None:
    with SQLiteOperationQueue(tmp_path / "queue.sqlite3", max_batch_size=1) as queue:
        first, second = operation(), operation(entity_id="task-2")
        queue.enqueue(first)
        queue.enqueue(second)
        assert len(queue.claim_batch(now=NOW, limit=500)) == 1
        with pytest.raises(ValueError, match="limit must be positive"):
            queue.claim_batch(now=NOW, limit=0)


def test_malformed_stored_record_is_inspectable_then_failed(tmp_path: Path) -> None:
    path = tmp_path / "queue.sqlite3"
    with SQLiteOperationQueue(path) as queue:
        queue.enqueue(operation())
        queue._connection.execute(
            "UPDATE offline_operations SET operation_json = 'not-json' WHERE sequence = 1"
        )
        inspected = queue.inspect()[0]
        assert inspected.malformed
        assert inspected.operation is None
        assert queue.diagnostics().malformed == 1
        assert queue.claim_batch(now=NOW) == ()
        failed = queue.inspect()[0]
        assert failed.status == QueueStatus.FAILED
        assert failed.error_category == ErrorCategory.MALFORMED
        assert failed.operation is None


def test_concurrent_duplicate_enqueue_across_connections_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "queue.sqlite3"
    operation_value = operation()
    queues = [SQLiteOperationQueue(path) for _ in range(8)]
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda queue: queue.enqueue(operation_value), queues))
        assert len({entry.operation_id for entry in results}) == 1
        assert len(queues[0].inspect()) == 1
    finally:
        for queue in queues:
            queue.close()


def test_storage_failure_does_not_report_success_or_drop_existing_rows(tmp_path: Path) -> None:
    with SQLiteOperationQueue(tmp_path / "queue.sqlite3") as queue:
        existing = operation()
        queue.enqueue(existing)
        queue._connection.execute(
            """CREATE TRIGGER refuse_offline_insert BEFORE INSERT ON offline_operations
               BEGIN SELECT RAISE(ABORT, 'disk unavailable'); END"""
        )
        with pytest.raises(QueueStorageError, match="disk unavailable"):
            queue.enqueue(operation(entity_id="task-2"))
        assert [entry.operation_id for entry in queue.inspect()] == [str(existing.operation_id)]


def test_cancellation_only_before_first_send_without_unresolved_dependents(
    tmp_path: Path,
) -> None:
    parent = operation()
    child = operation(entity_id="task-2", depends_on=(parent.operation_id,))
    with SQLiteOperationQueue(tmp_path / "queue.sqlite3") as queue:
        queue.enqueue(parent)
        queue.enqueue(child)
        with pytest.raises(QueueStateError, match="dependent"):
            queue.cancel(parent.operation_id, now=NOW)
        queue.cancel(child.operation_id, now=NOW)
        assert queue.cancel(parent.operation_id, now=NOW).status == QueueStatus.CANCELLED

    with SQLiteOperationQueue(tmp_path / "sent.sqlite3") as queue:
        op = operation()
        queue.enqueue(op)
        queue.claim_batch(now=NOW)
        with pytest.raises(QueueStateError, match="may have reached the server"):
            queue.cancel(op.operation_id, now=NOW)
