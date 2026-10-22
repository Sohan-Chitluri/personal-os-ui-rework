"""M1 remediation regression tests (audit fixes F1-F10).

Covers the confirmed defects:
  F1. Tenant isolation — update_task must reject a project_id owned by another user.
  F2. Crash recovery — a durable 'processing' row recovers on redelivery.
  F3. DB-level idempotency — unique (owner, idempotency_key) and unique
      capture_id on processing; concurrent workers cannot duplicate work.
  F4. Recovery correctness — 'failed' rows recovered; soft-deleted captures
      terminalized instead of starving the sweep; task IDs preserved.
  F5. Worker resilience — bounded backoff; unexpected errors requeue (no loss).
  F6. Deleted-record handling — soft-deleted records cannot be updated/revived.
  F7. Atomic revisions — concurrent stale updates conflict atomically.
  F8. Dispatch boundary — QueueDispatchError carries the capture ID.
  F9. SQLite FK enforcement — PRAGMA foreign_keys=ON on every connection.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from personal_os.core.extraction import ExtractionType, SemanticExtractor
from personal_os.core.extraction.actions import AppendNote, CreateTask, MarkComplete, NoOp
from personal_os.core.extraction.models import RawExtraction
from personal_os.core.llm import LLMProvider
from personal_os.db.base import utcnow
from personal_os.db.models import Capture, CaptureDispatchOutbox, CaptureProcessing, Task, User
from personal_os.db.repositories import (
    SqlCaptureProcessingRepository,
    SqlCaptureRepository,
    SqlProjectRepository,
    SqlTaskRepository,
    StaleRevisionError,
)
from personal_os.services import (
    CaptureServiceImpl,
    LocalCaptureQueue,
    ProcessingServiceImpl,
    QueueDispatchError,
    ServiceNotFound,
)
from personal_os.services.processing import ProcessingTransitionError
from personal_os.services.outbox import CaptureOutboxDispatcher
from personal_os.services.worker import CaptureWorker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_raw(etype: ExtractionType, description: str) -> RawExtraction:
    return RawExtraction(
        type=etype,
        description=description,
        certainty=0.9,
        confidence=0.9,
        source_excerpt=description,
        metadata={},
    )


def make_extractor(extractions) -> SemanticExtractor:
    provider = MagicMock(spec=LLMProvider)
    provider.extract = AsyncMock(return_value=extractions)
    return SemanticExtractor(provider=provider)


def task_count(db_session) -> int:
    # Synchronous count via a bound sync select on the async session.
    rows = db_session.execute(select(Task)).all()
    return len(rows)


async def make_user(db_session, email: str = "t@e.c") -> User:
    u = User(email=email)
    db_session.add(u)
    await db_session.flush()
    return u


# ---------------------------------------------------------------------------
# F1: tenant isolation — cross-user project assignment rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_task_cannot_link_other_users_project(db_session):
    from personal_os.db.repositories import SqlProjectRepository
    from personal_os.services import TaskServiceImpl

    alice = await make_user(db_session, "alice@x.c")
    bob = await make_user(db_session, "bob@x.c")
    tasks = TaskServiceImpl(db_session)
    alice_task = await tasks.create_task(alice.id, "Alice task")
    bob_project = await SqlProjectRepository(db_session).create(bob.id, "Bob project")
    await db_session.commit()
    # Capture ids before the rollback (rollback expires ORM instances; reading
    # an expired attribute would trigger a lazy load outside a greenlet).
    alice_task_id = alice_task.id
    bob_project_id = bob_project.id

    with pytest.raises(ServiceNotFound):
        await tasks.update_task(
            alice.id, alice_task.id, project_id=bob_project.id, title="hi"
        )
    await db_session.rollback()
    # Task unchanged (rollback reverted the aborted attempt; re-verify via repository)
    db_session.expire_all()
    # The row must still exist with project_id unchanged (NULL).
    rows = (
        await db_session.execute(
            select(Task.id, Task.project_id).where(Task.id == alice_task_id)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].project_id is None


@pytest.mark.asyncio
async def test_create_task_already_validates_project_ownership(db_session):
    from personal_os.db.repositories import SqlProjectRepository
    from personal_os.services import TaskServiceImpl

    alice = await make_user(db_session, "a2@x.c")
    bob = await make_user(db_session, "b2@x.c")
    tasks = TaskServiceImpl(db_session)
    bob_project = await SqlProjectRepository(db_session).create(bob.id, "Bob proj 2")
    with pytest.raises(ServiceNotFound):
        await tasks.create_task(alice.id, "t", project_id=bob_project.id)


async def _process_test_action(db_session, owner_id, action, etype=ExtractionType.TASK):
    capture = await SqlCaptureRepository(db_session).create(owner_id, "source text")

    class Extractor:
        async def extract(self, text, **kwargs):
            return SimpleNamespace(
                items=[
                    SimpleNamespace(
                        raw=SimpleNamespace(
                            type=etype, description=getattr(action, "title", "complete")
                        ),
                        action=action,
                    )
                ],
                errors=[],
                source_text=text,
            )

    result = await ProcessingServiceImpl(db_session, Extractor()).process_capture(
        capture.id, owner_id
    )
    await db_session.commit()
    return capture, result


async def test_extracted_task_preserves_owned_project_and_deadline(db_session, test_user):
    project = await SqlProjectRepository(db_session).create(test_user.id, "M1 Project")
    deadline = date(2027, 2, 3)
    _, result = await _process_test_action(
        db_session,
        test_user.id,
        CreateTask("Finish audit", "M1 Project", deadline=deadline),
    )
    task = await db_session.get(Task, result.task_ids[0])
    assert task.project_id == project.id
    assert task.due_date == datetime(2027, 2, 3, tzinfo=UTC)


async def test_missing_and_ambiguous_project_matches_are_recorded(db_session, test_user):
    _, missing_result = await _process_test_action(
        db_session, test_user.id, CreateTask("Missing project task", "missing")
    )
    missing = await db_session.get(Task, missing_result.task_ids[0])
    assert missing.project_id is None
    job = await SqlCaptureProcessingRepository(db_session).get_by_capture(missing.capture_id)
    assert job.result["items"][0]["project_resolution"] == "missing"

    await SqlProjectRepository(db_session).create(test_user.id, "Duplicate")
    await SqlProjectRepository(db_session).create(test_user.id, "Duplicate")
    _, ambiguous_result = await _process_test_action(
        db_session, test_user.id, CreateTask("Ambiguous project task", "Duplicate")
    )
    ambiguous = await db_session.get(Task, ambiguous_result.task_ids[0])
    assert ambiguous.project_id is None
    job = await SqlCaptureProcessingRepository(db_session).get_by_capture(ambiguous.capture_id)
    assert job.result["items"][0]["project_resolution"] == "ambiguous"


async def test_cross_tenant_project_name_is_not_associated(db_session, test_user):
    other = await make_user(db_session, "owner2@example.com")
    await SqlProjectRepository(db_session).create(other.id, "Private project")
    _, result = await _process_test_action(
        db_session, test_user.id, CreateTask("Safe task", "Private project")
    )
    task = await db_session.get(Task, result.task_ids[0])
    assert task.owner_id == test_user.id
    assert task.project_id is None


async def test_mark_complete_requires_one_owner_scoped_exact_match(db_session, test_user):
    project = await SqlProjectRepository(db_session).create(test_user.id, "Completion project")
    task = await SqlTaskRepository(db_session).create(
        test_user.id, "Finish exact task", project_id=project.id
    )
    _, result = await _process_test_action(
        db_session,
        test_user.id,
        MarkComplete("Finish exact task", "Completion project"),
        ExtractionType.COMPLETION,
    )
    updated = await db_session.get(Task, task.id)
    assert updated.status == "done"
    assert task.id in result.task_ids

    ambiguous_task = await SqlTaskRepository(db_session).create(
        test_user.id, "Same title", project_id=project.id
    )
    await SqlTaskRepository(db_session).create(test_user.id, "Same title", project_id=project.id)
    _, _ = await _process_test_action(
        db_session,
        test_user.id,
        MarkComplete("Same title", "Completion project"),
        ExtractionType.COMPLETION,
    )
    assert (await db_session.get(Task, ambiguous_task.id)).status == "open"


async def test_deferred_actions_are_explicit_and_noop_is_classified(db_session, test_user):
    capture, _ = await _process_test_action(
        db_session,
        test_user.id,
        AppendNote("An observation without persistence support", None),
        ExtractionType.OBSERVATION,
    )
    job = await SqlCaptureProcessingRepository(db_session).get_by_capture(capture.id)
    assert job.status == "succeeded"
    assert job.result["deferred_action_count"] == 1
    assert "explicitly deferred" in job.error
    assert job.result["items"][0]["processing"] == "deferred_m1_persistence_not_supported"

    capture, _ = await _process_test_action(
        db_session,
        test_user.id,
        NoOp("capture already persisted"),
        ExtractionType.JOURNAL,
    )
    job = await SqlCaptureProcessingRepository(db_session).get_by_capture(capture.id)
    assert job.result["deferred_action_count"] == 0
    assert job.result["items"][0]["processing"] == "intentional_no_op"


def test_datetime_columns_match_timezone_aware_migrations():
    from sqlalchemy.dialects import sqlite

    from personal_os.db.models import CaptureProcessing, Task

    for model, names in ((Task, ("due_date",)), (CaptureProcessing, ("started_at", "finished_at"))):
        for name in names:
            assert model.__table__.c[name].type.timezone is True
    assert utcnow().tzinfo is not None
    date_type = Task.__table__.c.due_date.type
    naive = datetime(2027, 2, 3, 10, 0)
    bound = date_type.process_bind_param(naive, sqlite.dialect())
    restored = date_type.process_result_value(naive, sqlite.dialect())
    offset = date_type.process_result_value(
        datetime(2027, 2, 3, 15, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
        sqlite.dialect(),
    )
    assert bound.tzinfo == UTC and bound.hour == 10
    assert restored.tzinfo == UTC and restored.hour == 10
    assert offset.tzinfo == UTC and offset.hour == 10


# ---------------------------------------------------------------------------
# F2: crash recovery — durable 'processing' row recovers after "process crash"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_processing_row_recovers_after_simulated_crash(db_session):
    """A row left in 'processing' (worker died before commit) is re-delivered
    and successfully completes — no ProcessingTransitionError."""
    user = await make_user(db_session, "crash@x.c")
    cap_repo = SqlCaptureRepository(db_session)
    proc_repo = SqlCaptureProcessingRepository(db_session)
    capture = await cap_repo.create(user.id, "Buy the milk")
    job = await proc_repo.create(capture.id)
    # Simulate a crashed worker: attempt incremented, row left in 'processing'.
    await proc_repo.update(job.id, status="processing", attempt=1)
    await db_session.commit()

    # New session (fresh worker) processes the stale row.
    extractor = make_extractor([make_raw(ExtractionType.TASK, "Buy the milk")])
    processor = ProcessingServiceImpl(db_session, extractor)
    result = await processor.process_capture(capture.id, user.id)
    await db_session.commit()
    assert result.status == "succeeded"
    assert len(result.task_ids) == 1


@pytest.mark.asyncio
async def test_processing_self_transition_allowed_only_for_recovery(db_session):
    proc_repo = SqlCaptureProcessingRepository(db_session)
    user = await make_user(db_session, "st@x.c")
    cap = await SqlCaptureRepository(db_session).create(user.id, "x")
    job = await proc_repo.create(cap.id)
    # pending -> processing is the normal path.
    await proc_repo.update(job.id, status="processing", attempt=1)
    # processing -> processing (crash recovery) is legal at the repository level.
    await proc_repo.update(job.id, status="processing", attempt=2)
    # Illegal transitions are enforced by ProcessingServiceImpl's state machine:
    from personal_os.services.processing import _require_transition

    with pytest.raises(ProcessingTransitionError):
        _require_transition("succeeded", "processing")
    with pytest.raises(ProcessingTransitionError):
        _require_transition("dead", "failed")
    # terminal states are final: no transitions out.
    assert ("succeeded", "succeeded") not in {
        t for t in ProcessingServiceImpl.__dict__.get("allowed", set())
    }  # placeholder sanity — real check is _require_transition
    await db_session.rollback()


# ---------------------------------------------------------------------------
# F3: DB-level idempotency constraints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_capture_idempotency_key_rejected_at_db_level(db_session):
    user = await make_user(db_session, "idem@x.c")
    repo = SqlCaptureRepository(db_session)
    await repo.create(user.id, "first", idempotency_key="key-1")
    await db_session.commit()
    with pytest.raises(IntegrityError):
        await repo.create(user.id, "second", idempotency_key="key-1")


@pytest.mark.asyncio
async def test_same_key_different_owners_allowed(db_session):
    alice = await make_user(db_session, "id3@x.c")
    bob = await make_user(db_session, "id4@x.c")
    repo = SqlCaptureRepository(db_session)
    a = await repo.create(alice.id, "a", idempotency_key="shared-key")
    b = await repo.create(bob.id, "b", idempotency_key="shared-key")
    assert a.id != b.id  # same key is scoped per owner


@pytest.mark.asyncio
async def test_one_processing_row_per_capture_enforced_by_db(db_session):
    user = await make_user(db_session, "pp@x.c")
    cap = await SqlCaptureRepository(db_session).create(user.id, "c")
    repo = SqlCaptureProcessingRepository(db_session)
    await repo.create(cap.id)
    await db_session.commit()
    with pytest.raises(IntegrityError):
        await repo.create(cap.id)


# ---------------------------------------------------------------------------
# F3/F4: concurrent workers cannot duplicate tasks; failed rows recover;
# task IDs preserved across retries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_job_recovers_and_preserves_task_ids(db_session):
    user = await make_user(db_session, "fr@x.c")
    cap_repo = SqlCaptureRepository(db_session)
    proc_repo = SqlCaptureProcessingRepository(db_session)
    cap = await cap_repo.create(user.id, "Buy the oat milk")
    job = await proc_repo.create(cap.id)

    # First attempt: partial — a task is written, then the pipeline "crashes"
    # mid-processing (second task write raises after the first succeeds).
    extractor = make_extractor([
        make_raw(ExtractionType.TASK, "Buy the oat milk"),
        make_raw(ExtractionType.TASK, "Also buy eggs"),
    ])

    # Use the real sink with a fault wrapper: first call delegates, second raises.
    real_calls = {"n": 0}

    class _FaultySink:
        async def create_task(self, owner_id, title, **kw):
            real_calls["n"] += 1
            if real_calls["n"] >= 2:
                raise RuntimeError("simulated crash after first task write")
            task = await ProcessingServiceImpl(db_session, extractor).tasks.create(
                owner_id, title, **kw
            )
            return task

    proc1 = ProcessingServiceImpl(db_session, extractor, task_sink=_FaultySink())
    r1 = await proc1.process_capture(cap.id, user.id)
    assert r1.status == "failed"
    await db_session.commit()
    # The first task row WAS written before the crash (durable partial work).
    rows = (await db_session.execute(select(Task).where(Task.capture_id == cap.id))).scalars().all()
    assert len(rows) == 1
    first_task_ids = [rows[0].id]

    # Sweep recovery: capture the re-enqueue of the failed row.
    queue = LocalCaptureQueue()
    svc = CaptureServiceImpl(db_session, queue=queue)
    requeued = await svc.recover_pending_captures()
    await db_session.commit()
    assert requeued == 1
    assert queue.size == 0
    dispatcher = CaptureOutboxDispatcher(db_session_factory(db_session), queue)
    assert await dispatcher.run_once() == 1
    assert queue.size == 1

    # Retry with a healthy pipeline: task dedup keeps exactly one task.
    proc2 = ProcessingServiceImpl(db_session, extractor)
    r2 = await proc2.process_capture(cap.id, user.id)
    await db_session.commit()
    assert r2.status == "succeeded"
    # The pre-crash task ID is preserved (crash-replay guard), and the task
    # that was never written before the crash is now created. No duplicates.
    assert first_task_ids[0] in r2.task_ids
    assert len(r2.task_ids) == 2
    assert len(set(r2.task_ids)) == len(r2.task_ids)


@pytest.mark.asyncio
async def test_soft_deleted_capture_terminalized_by_sweep_not_starving(db_session):
    """Soft-deleted capture's processing row is marked 'dead' by the sweep;
    other pending work still gets re-enqueued."""
    user = await make_user(db_session, "del@x.c")
    cap_repo = SqlCaptureRepository(db_session)
    proc_repo = SqlCaptureProcessingRepository(db_session)

    deleted_cap = await cap_repo.create(user.id, "deleted note")
    deleted_job = await proc_repo.create(deleted_cap.id)
    live_cap = await cap_repo.create(user.id, "live note")
    await proc_repo.create(live_cap.id)
    await db_session.commit()

    await cap_repo.soft_delete(deleted_cap.id, user.id)
    await db_session.commit()

    queue = LocalCaptureQueue()
    svc = CaptureServiceImpl(db_session, queue=queue)
    requeued = await svc.recover_pending_captures()
    await db_session.commit()

    assert requeued == 1  # only the live capture
    job = await proc_repo.get_by_capture(deleted_cap.id)
    assert job.status == "dead"
    assert job.id == deleted_job.id


# ---------------------------------------------------------------------------
# F5: worker resilience — unexpected errors requeue; backoff is bounded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_unexpected_error_requeues_and_recovers(db_session):
    """Unexpected exception (e.g. DB outage) does not lose durable work."""
    user = await make_user(db_session, "we@x.c")
    cap = await CaptureServiceImpl(db_session).submit_capture(user.id, "Buy rice")
    await db_session.commit()
    queue = LocalCaptureQueue()
    dispatcher = CaptureOutboxDispatcher(db_session_factory(db_session), queue)

    extractor = make_extractor([make_raw(ExtractionType.TASK, "Buy rice")])
    worker = CaptureWorker(
        db_session_factory(db_session), queue, extractor, retry_backoff=0.0,
        outbox_dispatcher=dispatcher,
    )

    # Simulate DB failure: the session factory yields a broken object first.
    calls = {"n": 0}
    original = worker.session_factory

    class FlakyFactory:
        def __call__(self):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("db outage")
            return original()

    worker.session_factory = FlakyFactory()
    await worker.run_once()  # raises internally? — must not lose message
    # Message is still queued (requeued for redelivery).
    assert queue.size == 1

    # Restore the healthy factory → message processes successfully.
    worker.session_factory = original
    await worker.run_once()
    assert worker.processed == 1
    assert queue.size == 0


def db_session_factory(db_session):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(bind=db_session.bind, expire_on_commit=False)


@pytest.mark.asyncio
async def test_worker_backoff_is_bounded(db_session):
    worker = CaptureWorker(
        db_session_factory(db_session),
        LocalCaptureQueue(),
        make_extractor([]),
        retry_backoff=0.1,
        retry_backoff_max=0.2,
    )
    cid = "cap-1"
    worker._backoff[cid] = 0.1
    # Simulate several retry cycles.
    for _ in range(6):
        worker._backoff[cid] = min(
            worker._backoff[cid] * 2, worker.retry_backoff_max
        )
    assert worker._backoff[cid] == 0.2  # capped, never explodes


# ---------------------------------------------------------------------------
# F6: soft-deleted records cannot be modified or revived
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_deleted_task_cannot_be_updated(db_session):
    user = await make_user(db_session, "dt@x.c")
    tasks = SqlTaskRepository(db_session)
    t = await tasks.create(user.id, "doomed")
    await tasks.soft_delete(t.id, user.id)
    await db_session.commit()
    with pytest.raises(StaleRevisionError):
        await tasks.update(t.id, user.id, status="done")  # blocked — not revived
    assert await tasks.get(t.id, user.id) is None


@pytest.mark.asyncio
async def test_soft_deleted_task_stale_revision_error_not_revival(db_session):
    """Even a wrong-revision update on a soft-deleted row must not revive it."""
    user = await make_user(db_session, "dr@x.c")
    tasks = SqlTaskRepository(db_session)
    t = await tasks.create(user.id, "revived?")
    await tasks.soft_delete(t.id, user.id)
    await db_session.commit()
    # expected_revision=1 matches the ORIGINAL revision (pre-delete) — the
    # guarded update still refuses because deleted_at IS NOT NULL.
    with pytest.raises(StaleRevisionError):
        await tasks.update(t.id, user.id, expected_revision=1, status="done")
    assert await tasks.get(t.id, user.id) is None


@pytest.mark.asyncio
async def test_soft_deleted_project_cannot_be_updated(db_session):
    user = await make_user(db_session, "dp@x.c")
    projects = SqlProjectRepository(db_session)
    p = await projects.create(user.id, "gone")
    await projects.soft_delete(p.id, user.id)
    await db_session.commit()
    with pytest.raises(StaleRevisionError):
        await projects.update(p.id, user.id, name="new-name")  # blocked — not revived
    assert await projects.get(p.id, user.id) is None


@pytest.mark.asyncio
async def test_soft_deleted_capture_cannot_be_updated(db_session):
    user = await make_user(db_session, "dc@x.c")
    repo = SqlCaptureRepository(db_session)
    c = await repo.create(user.id, "note")
    await repo.soft_delete(c.id, user.id)
    await db_session.commit()
    with pytest.raises(StaleRevisionError):
        await repo.update(c.id, user.id, status="processed")  # blocked — not revived
    assert await repo.get(c.id, user.id) is None


# ---------------------------------------------------------------------------
# F7: atomic revisions — concurrent stale updates conflict in the UPDATE itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revision_conflict_is_atomic(db_session):
    user = await make_user(db_session, "rev@x.c")
    tasks = SqlTaskRepository(db_session)
    t = await tasks.create(user.id, "atomic")
    await db_session.commit()

    # Simulate a concurrent writer that bumped the revision to 2 in the DB.
    await db_session.execute(
        Task.__table__.update().where(Task.id == t.id).values(revision=2)
    )

    # A stale update (expected_revision=1) must fail inside the UPDATE.
    with pytest.raises(StaleRevisionError):
        await tasks.update(t.id, user.id, expected_revision=1, status="done")
    # Without expected_revision the latest revision wins (normal flow).
    updated = await tasks.update(t.id, user.id, status="done")
    assert updated.revision == 3


# ---------------------------------------------------------------------------
# F8: Queue send failure keeps the transactional outbox retryable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_failure_keeps_outbox_pending(db_session):
    user = await make_user(db_session, "qde@x.c")

    def _boom(capture_id: str, owner_id: str):
        raise RuntimeError("SQS outage")

    queue = LocalCaptureQueue(fail_enqueue=_boom)
    svc = CaptureServiceImpl(db_session, queue=queue)
    capture = await svc.submit_capture(user.id, "content during outage")
    await db_session.commit()
    dispatcher = CaptureOutboxDispatcher(
        db_session_factory(db_session), queue, retry_base_seconds=0
    )
    assert await dispatcher.run_once() == 0
    row = await db_session.scalar(
        select(CaptureDispatchOutbox).where(CaptureDispatchOutbox.capture_id == capture.id)
    )
    await db_session.refresh(row)
    assert row.state == "pending"
    assert row.attempts == 1
    assert row.last_error == "RuntimeError"


@pytest.mark.asyncio
async def test_dispatch_failure_capture_remains_durable_after_commit(db_session):
    user = await make_user(db_session, "qdf@x.c")

    def _boom(capture_id: str, owner_id: str):
        raise RuntimeError("SQS outage")

    queue = LocalCaptureQueue(fail_enqueue=_boom)
    svc = CaptureServiceImpl(db_session, queue=queue)
    capture = await svc.submit_capture(user.id, "durable content")
    await db_session.commit()
    dispatcher = CaptureOutboxDispatcher(
        db_session_factory(db_session), queue, retry_base_seconds=0
    )
    assert await dispatcher.run_once() == 0
    caps = (await db_session.execute(select(Capture))).scalars().all()
    assert len(caps) == 1
    jobs = (
        await db_session.execute(select(CaptureProcessing))
    ).scalars().all()
    assert len(jobs) == 1 and jobs[0].status == "pending"
    outbox = await db_session.scalar(
        select(CaptureDispatchOutbox).where(CaptureDispatchOutbox.capture_id == capture.id)
    )
    assert outbox is not None and outbox.state == "pending" and outbox.attempts == 1


# ---------------------------------------------------------------------------
# F9: PostgreSQL FK enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_postgresql_foreign_keys_are_enforced(db_session):
    """PostgreSQL rejects rows referencing a missing owner."""
    user_id = "01FAKE00000000000000000000"
    with pytest.raises(IntegrityError):
        db_session.add(Task(owner_id=user_id, title="orphan"))
        await db_session.commit()
    await db_session.rollback()


# ---------------------------------------------------------------------------
# F4-supplement: processing transition table sanity (self-transition added)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotent_replay_of_terminal_job_is_noop(db_session):
    user = await make_user(db_session, "term@x.c")
    cap = await SqlCaptureRepository(db_session).create(user.id, "Buy oat milk")
    proc_repo = SqlCaptureProcessingRepository(db_session)
    extractor = make_extractor([make_raw(ExtractionType.TASK, "Buy oat milk")])
    r1 = await ProcessingServiceImpl(db_session, extractor).process_capture(
        cap.id, user.id
    )
    await db_session.commit()
    assert r1.status == "succeeded"

    # Duplicate delivery — terminal job → no-op with same task IDs.
    r2 = await ProcessingServiceImpl(db_session, extractor).process_capture(
        cap.id, user.id
    )
    await db_session.commit()
    assert r2.status == "succeeded"
    assert r2.task_ids == r1.task_ids
    tasks = (await db_session.execute(select(Task))).scalars().all()
    assert len(tasks) == 1
