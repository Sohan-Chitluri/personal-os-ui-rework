"""M1-B tests: capture processing pipeline, queue, worker, failure injection.

Failure-injection coverage (per milestone requirements):
  * dispatch failure — queue.enqueue raises AFTER DB writes
  * worker crash — pipeline raises mid-processing
  * retry — retryable failure → requeue → success on attempt 2
  * duplicate delivery — message processed twice → idempotent no-op
  * partial processing — crash between task writes and status update
  * retry safety — no duplicate tasks or projects across retries
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from personal_os.core.extraction import ExtractionType, SemanticExtractor
from personal_os.core.extraction.models import RawExtraction
from personal_os.core.llm import LLMProvider, LLMProviderError
from personal_os.db.models import Task
from personal_os.db.repositories import SqlCaptureRepository, SqlTaskRepository
from personal_os.services import (
    CaptureServiceImpl,
    LocalCaptureQueue,
    ProcessingServiceImpl,
    ServiceNotFound,
)
from personal_os.services.outbox import CaptureOutboxDispatcher
from personal_os.services.worker import CaptureWorker


# ---------------------------------------------------------------------------
# Helpers + fixtures
# ---------------------------------------------------------------------------


def make_raw(etype: ExtractionType, description: str, *, certainty: float = 0.9) -> RawExtraction:
    return RawExtraction(
        type=etype,
        description=description,
        certainty=certainty,
        confidence=0.9,
        source_excerpt=description,
        metadata={},
    )


def make_provider(extractions):
    provider = MagicMock(spec=LLMProvider)
    provider.extract = AsyncMock(return_value=extractions)
    return provider


def make_extractor(extractions) -> SemanticExtractor:
    return SemanticExtractor(provider=make_provider(extractions))


def task_count(db_session) -> int:
    return len(db_session.execute(select(Task)).scalars().all())


async def count_tasks_async(db_session) -> int:
    rows = await db_session.execute(select(Task))
    return len(rows.scalars().all())


def dispatcher_for(db_session, queue, *, retry_base_seconds=1):
    factory = async_sessionmaker(
        bind=db_session.bind,
        expire_on_commit=False,
        autoflush=False,
    )
    return CaptureOutboxDispatcher(
        factory,
        queue,
        retry_base_seconds=retry_base_seconds,
        retry_max_seconds=retry_base_seconds,
    )


@pytest_asyncio.fixture
async def queue():
    return LocalCaptureQueue()


@pytest_asyncio.fixture
async def capture_service(db_session, queue):
    return CaptureServiceImpl(db_session, queue=queue)


# ---------------------------------------------------------------------------
# 1. Local queue basics
# ---------------------------------------------------------------------------


class TestLocalQueue:
    async def test_enqueue_dequeue_ack(self, queue):
        await queue.enqueue("cap1", "user1")
        assert queue.size == 1
        msg = await queue.dequeue()
        assert msg == ("cap1", "user1")
        assert queue.in_flight == 1
        await queue.ack(msg)
        assert queue.in_flight == 0
        assert await queue.dequeue() is None

    async def test_requeue_returns_message_for_redelivery(self, queue):
        await queue.enqueue("cap1", "user1")
        await queue.dequeue()
        queue.requeue_message(("cap1", "user1"))
        assert queue.size == 1
        assert queue.redelivery_count("cap1") == 1
        assert (await queue.dequeue()) == ("cap1", "user1")

    async def test_fail_enqueue_hook_raises(self, queue):
        def boom(cid, oid):
            raise RuntimeError("outage")

        q = LocalCaptureQueue(fail_enqueue=boom)
        with pytest.raises(RuntimeError):
            await q.enqueue("cap1", "user1")
        assert q.size == 0  # nothing enqueued — dispatch truly failed


# ---------------------------------------------------------------------------
# 2. Submission + dispatch boundary (persist-first)
# ---------------------------------------------------------------------------


class TestSubmissionAndDispatch:
    async def test_submit_persists_then_dispatches(self, db_session, test_user, capture_service, queue):
        c = await capture_service.submit_capture(test_user.id, "hello world")
        await db_session.commit()
        got = await capture_service.get_capture(test_user.id, c.id)
        assert got.status == "pending"
        assert queue.size == 0  # producer commits outbox intent; dispatcher sends later
        assert await dispatcher_for(db_session, queue).run_once() == 1
        assert queue.size == 1
        assert (await queue.dequeue()) == (c.id, test_user.id)

    async def test_dispatch_failure_leaves_capture_durable(self, db_session, test_user, queue):
        def boom(capture_id, owner_id):
            raise RuntimeError("SQS unavailable")

        broken = LocalCaptureQueue(fail_enqueue=boom)
        svc = CaptureServiceImpl(db_session, queue=broken)
        capture = await svc.submit_capture(test_user.id, "durable content")
        await db_session.commit()
        dispatcher = dispatcher_for(db_session, broken, retry_base_seconds=0)
        assert await dispatcher.run_once() == 0

        # Capture + processing row survive dispatch failure — recoverable.
        captures = await svc.list_captures(test_user.id)
        assert len(captures) == 1
        assert captures[0].content == "durable content"
        job = await svc.processing.get_by_capture(captures[0].id)
        assert job is not None and job.status == "pending"
        assert capture.id == captures[0].id
        assert broken.size == 0

    async def test_recovery_sweep_reenqueues_pending(self, db_session, test_user, queue):
        def boom(capture_id, owner_id):
            raise RuntimeError("dispatch outage")

        broken = LocalCaptureQueue(fail_enqueue=boom)
        svc = CaptureServiceImpl(db_session, queue=broken)
        capture = await svc.submit_capture(test_user.id, "needs recovery")
        await db_session.commit()
        assert await dispatcher_for(db_session, broken, retry_base_seconds=0).run_once() == 0

        # Outage passes: retry the durable outbox row to a healthy queue.
        healthy = LocalCaptureQueue()
        requeued = await dispatcher_for(db_session, healthy, retry_base_seconds=0).run_once()
        assert requeued == 1
        assert healthy.size == 1
        assert queue.size == 0
        assert (await healthy.dequeue()) == (capture.id, test_user.id)

    async def test_idempotent_replay_does_not_reenqueue(self, db_session, test_user, capture_service, queue):
        first = await capture_service.submit_capture(test_user.id, "once", idempotency_key="k1")
        await db_session.commit()
        assert await dispatcher_for(db_session, queue).run_once() == 1
        assert queue.size == 1
        # Replay with the same key → same capture returned, no second dispatch.
        again = await capture_service.submit_capture(test_user.id, "twice", idempotency_key="k1")
        assert again.id == first.id
        assert await dispatcher_for(db_session, queue).run_once() == 0
        assert queue.size == 1  # no new message

    async def test_recover_pending_captures_requires_queue(self, db_session, capture_service):
        # No queue configured → recovery is a no-op, not an error.
        assert await capture_service.recover_pending_captures() == 0


# ---------------------------------------------------------------------------
# 3. Processing pipeline (existing extractor reused via service layer)
# ---------------------------------------------------------------------------


class TestProcessing:
    async def test_provider_failure_is_recoverable_not_reported_as_success(
        self, db_session, test_user, capture_service
    ):
        cap = await capture_service.submit_capture(test_user.id, "Remember the appointment")
        await db_session.commit()
        provider = MagicMock(spec=LLMProvider)
        provider.extract = AsyncMock(side_effect=LLMProviderError("OPENROUTER_API_KEY is not set"))
        processor = ProcessingServiceImpl(db_session, SemanticExtractor(provider=provider))

        result = await processor.process_capture(cap.id, test_user.id)
        await db_session.commit()

        assert result.status == "failed"
        assert "OPENROUTER_API_KEY is not set" in (result.error or "")
        job = await processor.processing.get_by_capture(cap.id)
        assert job.status == "failed"
        assert job.attempt == 1

    async def test_full_pipeline_creates_task(
        self, db_session, test_user, capture_service, queue
    ):
        cap = await capture_service.submit_capture(test_user.id, "I need to fix the login bug")
        await db_session.commit()
        processor = ProcessingServiceImpl(db_session, make_extractor(
            [make_raw(ExtractionType.TASK, "Fix the login bug")]
        ))

        result = await processor.process_capture(cap.id, test_user.id)
        await db_session.commit()

        assert result.status == "succeeded"
        assert len(result.task_ids) == 1

        task = await db_session.get(Task, result.task_ids[0])
        assert task.title == "Fix the login bug"
        assert task.capture_id == cap.id
        assert task.owner_id == test_user.id

        # Explicit state transitions happened.
        cap_after = await processor.captures.get(cap.id, test_user.id)
        assert cap_after.status == "processed"
        job = await processor.processing.get_by_capture(cap.id)
        assert job.status == "succeeded"
        assert job.attempt == 1
        assert job.finished_at is not None
        assert job.task_ids == result.task_ids
        # Full pipeline trace recorded (audit of every action, incl. non-task).
        assert job.result["source_text_preserved"] is True

    async def test_duplicate_delivery_is_idempotent_noop(
        self, db_session, test_user, capture_service
    ):
        cap = await capture_service.submit_capture(test_user.id, "dedupe me")
        await db_session.commit()
        processor = ProcessingServiceImpl(db_session, make_extractor(
            [make_raw(ExtractionType.TASK, "Dedupe task")]
        ))

        first = await processor.process_capture(cap.id, test_user.id)
        await db_session.commit()
        # Same message delivered again (SQS redelivery / local duplicate).
        second = await processor.process_capture(cap.id, test_user.id)
        assert second.status == "succeeded"
        assert second.task_ids == first.task_ids

        n = await count_tasks_async(db_session)
        assert n == 1  # exactly one task — no duplicate write

    async def test_journal_capture_produces_no_task_no_project(
        self, db_session, test_user, capture_service
    ):
        cap = await capture_service.submit_capture(test_user.id, "just venting")
        await db_session.commit()
        processor = ProcessingServiceImpl(db_session, make_extractor(
            [make_raw(ExtractionType.JOURNAL, "just venting")]
        ))
        result = await processor.process_capture(cap.id, test_user.id)
        await db_session.commit()
        assert result.status == "succeeded"
        assert result.task_ids == []
        assert await count_tasks_async(db_session) == 0

    async def test_processing_requires_known_capture(self, db_session, test_user):
        processor = ProcessingServiceImpl(db_session, make_extractor([]))
        with pytest.raises(ServiceNotFound):
            await processor.process_capture("01NOEXIST0000000000000000", test_user.id)

    async def test_dead_letter_after_max_attempts(self, db_session, test_user, capture_service):
        cap = await capture_service.submit_capture(test_user.id, "doomed")
        await db_session.commit()

        class AlwaysFails:
            async def create_task(self, owner_id, title, **kw):
                raise RuntimeError("permanent failure")

        processor = ProcessingServiceImpl(
            db_session, make_extractor([make_raw(ExtractionType.TASK, "Doomed")]),
            task_sink=AlwaysFails(),
        )
        statuses = []
        for _ in range(3):
            r = await processor.process_capture(cap.id, test_user.id)
            statuses.append(r.status)
        assert statuses == ["failed", "failed", "dead"]
        assert processor.processing.get_by_capture is not None

        # Attempt after terminal: idempotent no-op, no exception.
        r4 = await processor.process_capture(cap.id, test_user.id)
        assert r4.status == "dead"
        assert r4.task_ids == []


# ---------------------------------------------------------------------------
# 4. Failure injection: worker crash, retry, partial processing
# ---------------------------------------------------------------------------


class TestFailureInjection:
    async def test_worker_crash_then_retry_succeeds(self, db_session_factory, test_user, queue):
        """Pipeline raises on attempt 1 (worker crash), succeeds on attempt 2.
        Retry must not create duplicate tasks."""
        extractor = make_extractor([make_raw(ExtractionType.TASK, "Ship the fix")])
        attempts = {"n": 0}

        async with db_session_factory() as s0:
            cap = await CaptureServiceImpl(s0).submit_capture(test_user.id, "ship the fix")
            await s0.commit()
            cap_id = cap.id

        class FlakySink:
            async def create_task(self, owner_id, title, **kw):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise RuntimeError("worker crashed mid-pipeline")
                task = await SqlTaskRepository(s0).create(owner_id, title, **kw)
                return task

        async with db_session_factory() as s1:
            p1 = ProcessingServiceImpl(s1, extractor, task_sink=FlakySink())
            r1 = await p1.process_capture(cap_id, test_user.id)
            await s1.commit()
        assert r1.status == "failed"
        assert "crashed" in (r1.error or "")

        # attempt 2: healthy sink
        async with db_session_factory() as s2:
            p2 = ProcessingServiceImpl(s2, extractor)
            r2 = await p2.process_capture(cap_id, test_user.id)
            await s2.commit()
        assert r2.status == "succeeded"
        assert len(r2.task_ids) == 1
        assert attempts["n"] == 1  # FlakySink raised once; attempt 2 used default sink

        # exactly one task despite the crash+retry
        async with db_session_factory() as s3:
            assert await count_tasks_async(s3) == 1

    async def test_partial_processing_retry_no_duplicate_tasks(
        self, db_session_factory, test_user, queue
    ):
        """Crash AFTER task write, BEFORE status update — classic partial
        window. Retry must find the task already present and skip it."""
        extractor = make_extractor([make_raw(ExtractionType.TASK, "Write tests")])

        async with db_session_factory() as s0:
            cap = await CaptureServiceImpl(s0).submit_capture(test_user.id, "write tests")
            await s0.commit()
            cap_id = cap.id

        # Attempt 1: task written, then crash before status update.
        async with db_session_factory() as s1:
            await SqlTaskRepository(s1).create(test_user.id, "Write tests", capture_id=cap_id)
            await s1.commit()
            # (no status update — crash simulation)

        # Attempt 2 (fresh session = worker restart): pipeline re-runs, task
        # already exists for this capture → deduplicated, no new row.
        async with db_session_factory() as s2:
            p2 = ProcessingServiceImpl(s2, extractor)
            r2 = await p2.process_capture(cap_id, test_user.id)
            await s2.commit()
        assert r2.status == "succeeded"
        # Crash-replay guard: the pre-existing task's original ID surfaces in
        # task_ids — no new row is created and the ID is preserved.
        async with db_session_factory() as s2b:
            existing = (
                await s2b.execute(select(Task.id).where(Task.capture_id == cap_id))
            ).scalars().all()
        assert r2.task_ids == existing

        async with db_session_factory() as s3:
            assert await count_tasks_async(s3) == 1

    async def test_retry_after_failed_attempt_records_attempts(
        self, db_session_factory, test_user
    ):
        """The attempt counter must survive across sessions (crash restart)."""
        extractor = make_extractor([make_raw(ExtractionType.TASK, "Count me")])

        async with db_session_factory() as s0:
            cap = await CaptureServiceImpl(s0).submit_capture(test_user.id, "count me")
            await s0.commit()
            cap_id = cap.id

        class Boom:
            async def create_task(self, owner_id, title, **kw):
                raise RuntimeError("nope")

        async with db_session_factory() as s1:
            p1 = ProcessingServiceImpl(s1, extractor, task_sink=Boom())
            await p1.process_capture(cap_id, test_user.id)
            await s1.commit()

        async with db_session_factory() as s2:
            p2 = ProcessingServiceImpl(s2, extractor)
            job = await p2.processing.get_by_capture(cap_id)
            assert job.attempt == 1  # persisted across sessions
            assert job.status == "failed"


# ---------------------------------------------------------------------------
# 5. Worker loop
# ---------------------------------------------------------------------------


class TestWorker:
    async def test_worker_drains_queue(self, db_session_factory, test_user, queue):
        extractor = make_extractor([make_raw(ExtractionType.TASK, "Worker task")])

        async with db_session_factory() as s:
            svc = CaptureServiceImpl(s, queue=queue)
            for i in range(3):
                await svc.submit_capture(test_user.id, f"capture {i}")
            await s.commit()

        dispatcher = CaptureOutboxDispatcher(db_session_factory, queue)
        worker = CaptureWorker(db_session_factory, queue, extractor, outbox_dispatcher=dispatcher)
        await worker.run(max_messages=3)
        assert worker.processed == 3
        assert queue.size == 0 and queue.in_flight == 0

        async with db_session_factory() as s:
            assert await count_tasks_async(s) == 3

    async def test_worker_retryable_failure_requeues_then_succeeds(
        self, db_session_factory, test_user, queue
    ):
        """Retryable failure → requeue → redelivery → success. No dup tasks."""
        extractor = make_extractor([make_raw(ExtractionType.TASK, "Retry me")])
        calls = {"n": 0}

        async with db_session_factory() as s:
            svc = CaptureServiceImpl(s, queue=queue)
            cap = await svc.submit_capture(test_user.id, "retry me")
            await s.commit()
            cap_id = cap.id

        async def write_task(owner_id, title, **kw):
            async with db_session_factory() as s:
                t = await SqlTaskRepository(s).create(owner_id, title, **kw)
                await s.commit()
                return t

        class FlakyOnce:
            async def create_task(self, owner_id, title, **kw):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("transient")
                return await write_task(owner_id, title, **kw)

        # First delivery fails transiently; the same worker receives the
        # redelivery and succeeds without another producer send.
        dispatcher = CaptureOutboxDispatcher(db_session_factory, queue)
        worker = CaptureWorker(
            db_session_factory,
            queue,
            extractor,
            task_sink=FlakyOnce(),
            outbox_dispatcher=dispatcher,
        )
        await worker.run(max_messages=2)
        assert worker.processed == 1
        assert worker.retried == 1
        assert calls["n"] == 2  # first attempt failed, second attempt succeeded

        async with db_session_factory() as s:
            assert await count_tasks_async(s) == 1

    async def test_worker_recovery_sweep_on_startup(self, db_session_factory, test_user, queue):
        """Dispatch failed pre-crash → recovery sweep re-enqueues → worker
        processes it. DB is the record of truth; queue is only a hint."""
        extractor = make_extractor([make_raw(ExtractionType.TASK, "Recovered")])

        # Persist WITHOUT dispatch (simulates crash between commit & dispatch).
        async with db_session_factory() as s:
            cap = await CaptureServiceImpl(s).submit_capture(test_user.id, "recovered work")
            await s.commit()
            assert queue.size == 0  # never dispatched

        dispatcher = CaptureOutboxDispatcher(db_session_factory, queue)
        worker = CaptureWorker(db_session_factory, queue, extractor, outbox_dispatcher=dispatcher)
        n = await worker.recover_pending()
        assert n == 1
        assert queue.size == 1

        await worker.run(max_messages=1)
        assert worker.processed == 1

        async with db_session_factory() as s:
            assert await count_tasks_async(s) == 1

    async def test_worker_acks_terminal_dead_job(self, db_session_factory, test_user, queue):
        """Terminal failure (dead) → message acked, not requeued."""
        extractor = make_extractor([make_raw(ExtractionType.TASK, "Dead job")])

        async with db_session_factory() as s:
            cap = await CaptureServiceImpl(s).submit_capture(test_user.id, "dead job")
            await s.commit()
            cap_id = cap.id

        class AlwaysFails:
            async def create_task(self, owner_id, title, **kw):
                raise RuntimeError("permanent")

        # Exhaust retries outside the worker (attempts 1 and 2).
        for _ in range(2):
            async with db_session_factory() as s:
                p = ProcessingServiceImpl(s, extractor, task_sink=AlwaysFails())
                await p.process_capture(cap_id, test_user.id)
                await s.commit()

        # Third delivery hits max attempts → dead → worker acks.
        dispatcher = CaptureOutboxDispatcher(db_session_factory, queue)
        worker = CaptureWorker(
            db_session_factory,
            queue,
            extractor,
            task_sink=AlwaysFails(),
            outbox_dispatcher=dispatcher,
        )
        await worker.run(max_messages=1)
        assert worker.failed == 1
        assert worker.retried == 0
        assert queue.in_flight == 0  # acked, not requeued
        assert queue.size == 0

    async def test_worker_survives_missing_capture(self, db_session_factory, queue):
        """Poison message (capture gone) → acked, worker keeps running."""
        extractor = make_extractor([make_raw(ExtractionType.TASK, "Ghost")])
        await queue.enqueue("01GHOST00000000000000000000", "01GHOSTOWNER0000000000000")
        worker = CaptureWorker(db_session_factory, queue, extractor)
        await worker.run(max_messages=1)
        assert worker.processed == 0
        assert worker.retried == 0
        assert queue.in_flight == 0  # acked — no poison-message loop
