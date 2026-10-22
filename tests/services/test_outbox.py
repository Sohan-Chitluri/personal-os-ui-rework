"""PostgreSQL integration tests for transactional capture dispatch."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from personal_os.db.models import CaptureDispatchOutbox
from personal_os.services.outbox import CaptureOutboxDispatcher
from personal_os.services.queue import LocalCaptureQueue
from personal_os.services.tasks import CaptureServiceImpl


@pytest.mark.asyncio
async def test_capture_and_outbox_are_committed_before_queue_send(db_session, test_user):
    queue = LocalCaptureQueue()
    service = CaptureServiceImpl(db_session, queue=queue)
    capture = await service.submit_capture(test_user.id, "persist before dispatch")

    row = await db_session.scalar(
        select(CaptureDispatchOutbox).where(CaptureDispatchOutbox.capture_id == capture.id)
    )
    if row is not None:
        await db_session.refresh(row)
    assert row is not None
    assert row.state == "pending"
    assert queue.size == 0


@pytest.mark.asyncio
async def test_dispatch_failure_is_durable_and_backed_off(
    db_session, db_session_factory, test_user
):
    capture = await CaptureServiceImpl(db_session).submit_capture(test_user.id, "retry dispatch")
    await db_session.commit()

    class FailingQueue:
        async def enqueue(self, capture_id: str, owner_id: str) -> str:
            raise TimeoutError("transport details must not be persisted")

    dispatcher = CaptureOutboxDispatcher(
        db_session_factory,
        FailingQueue(),  # type: ignore[arg-type]
        retry_base_seconds=7,
    )
    before = datetime.now(UTC)
    assert await dispatcher.run_once() == 0

    row = await db_session.scalar(
        select(CaptureDispatchOutbox).where(CaptureDispatchOutbox.capture_id == capture.id)
    )
    if row is not None:
        await db_session.refresh(row)
    assert row is not None
    assert row.state == "pending"
    assert row.attempts == 1
    assert row.last_error == "TimeoutError"
    assert row.next_attempt_at is not None and row.next_attempt_at >= before


@pytest.mark.asyncio
async def test_two_dispatchers_do_not_publish_one_outbox_row_twice(
    db_session, db_session_factory, test_user
):
    capture = await CaptureServiceImpl(db_session).submit_capture(test_user.id, "single dispatch")
    await db_session.commit()

    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingQueue(LocalCaptureQueue):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def enqueue(self, capture_id: str, owner_id: str) -> str:
            self.calls += 1
            started.set()
            await release.wait()
            return await super().enqueue(capture_id, owner_id)

    queue = BlockingQueue()
    first = CaptureOutboxDispatcher(db_session_factory, queue)
    second = CaptureOutboxDispatcher(db_session_factory, queue)
    first_task = asyncio.create_task(first.run_once())
    await asyncio.wait_for(started.wait(), timeout=5)
    second_result = await second.run_once()
    release.set()
    first_result = await first_task

    assert (first_result, second_result) == (1, 0)
    assert queue.calls == 1
    assert queue.size == 1
    row = await db_session.scalar(
        select(CaptureDispatchOutbox).where(CaptureDispatchOutbox.capture_id == capture.id)
    )
    if row is not None:
        await db_session.refresh(row)
    assert row is not None and row.state == "dispatched"


@pytest.mark.asyncio
async def test_transaction_rollback_removes_capture_and_dispatch_intent(db_session, test_user):
    from personal_os.db.models import Capture

    capture = await CaptureServiceImpl(db_session).submit_capture(test_user.id, "rollback me")
    capture_id = capture.id
    await db_session.rollback()

    assert await db_session.get(Capture, capture_id) is None
    assert await db_session.scalar(
        select(CaptureDispatchOutbox.id).where(CaptureDispatchOutbox.capture_id == capture_id)
    ) is None
