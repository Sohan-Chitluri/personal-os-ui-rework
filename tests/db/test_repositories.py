"""Repository tests: CRUD, optimistic locking, idempotency, soft delete."""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from personal_os.db.models import Project, User
from personal_os.db.repositories import (
    SqlCaptureProcessingRepository,
    SqlCaptureRepository,
    SqlProjectRepository,
    SqlTaskRepository,
    SqlUserRepository,
    StaleRevisionError,
)
from personal_os.sync.postgres_store import SyncChangeEvent, SyncEntityRevision, SyncTombstone


@pytest_asyncio.fixture
async def repos(db_session):
    return {
        "users": SqlUserRepository(db_session),
        "projects": SqlProjectRepository(db_session),
        "tasks": SqlTaskRepository(db_session),
        "captures": SqlCaptureRepository(db_session),
        "processing": SqlCaptureProcessingRepository(db_session),
    }


@pytest_asyncio.fixture
async def owner(db_session, test_user):
    return test_user


# --- Users -----------------------------------------------------------------


async def test_user_get_or_create_by_sub_is_idempotent(repos):
    u1 = await repos["users"].get_or_create_by_sub("sub-abc", "a@example.com")
    u2 = await repos["users"].get_or_create_by_sub("sub-abc", "a@example.com")
    assert u1.id == u2.id


async def test_user_get_or_create_by_sub_concurrent(db_session_factory):
    """Concurrent requests for the same new subject must all succeed,
    return the exact same canonical user ID, and persist exactly one row.
    """
    sub = f"sub-concurrent-{uuid4().hex}"
    email = f"{sub}@example.com"
    concurrency = 12
    barrier = asyncio.Barrier(concurrency)

    async def _worker() -> str:
        async with db_session_factory() as session:
            repo = SqlUserRepository(session)
            await barrier.wait()
            user = await repo.get_or_create_by_sub(sub, email)
            user_id = user.id
            await session.commit()
            return user_id

    tasks = [_worker() for _ in range(concurrency)]
    user_ids = await asyncio.gather(*tasks)

    # All returned IDs must match
    assert len(set(user_ids)) == 1
    canonical_id = user_ids[0]

    # Verify exactly one row in PostgreSQL
    async with db_session_factory() as session:
        rows = (
            await session.execute(select(User).where(User.cognito_sub == sub))
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].id == canonical_id
        assert rows[0].email == email


async def test_user_get_or_create_by_sub_existing_user(db_session_factory):
    """An existing user is returned without attempting a new insert or altering email."""
    sub = f"sub-existing-{uuid4().hex}"
    email = f"{sub}@example.com"

    async with db_session_factory() as s1:
        u1 = await SqlUserRepository(s1).get_or_create_by_sub(sub, email)
        await s1.commit()
        u1_id = u1.id

    async with db_session_factory() as s2:
        u2 = await SqlUserRepository(s2).get_or_create_by_sub(sub, "different-email@example.com")
        assert u2.id == u1_id
        assert u2.email == email


async def test_user_get_or_create_by_sub_distinct_subjects_concurrent(db_session_factory):
    """Concurrent requests for distinct subjects all succeed independently."""
    concurrency = 8
    barrier = asyncio.Barrier(concurrency)
    subs = [f"sub-distinct-{uuid4().hex[:8]}-{i}" for i in range(concurrency)]

    async def _worker(subject: str) -> str:
        async with db_session_factory() as session:
            repo = SqlUserRepository(session)
            await barrier.wait()
            user = await repo.get_or_create_by_sub(subject, f"{subject}@example.com")
            user_id = user.id
            await session.commit()
            return user_id

    tasks = [_worker(subs[i]) for i in range(concurrency)]
    user_ids = await asyncio.gather(*tasks)

    # Every subject received a unique user ID
    assert len(set(user_ids)) == concurrency

    async with db_session_factory() as session:
        for sub, uid in zip(subs, user_ids):
            user = (
                await session.execute(select(User).where(User.cognito_sub == sub))
            ).scalar_one_or_none()
            assert user is not None
            assert user.id == uid


async def test_user_get_or_create_by_sub_conflicting_email_raises(db_session_factory):
    """IntegrityError caused by duplicate email on a distinct subject is preserved and raised."""
    shared_email = f"conflict-{uuid4().hex}@example.com"

    async with db_session_factory() as s1:
        await SqlUserRepository(s1).get_or_create_by_sub(f"sub-a-{uuid4().hex[:6]}", shared_email)
        await s1.commit()

    async with db_session_factory() as s2:
        repo = SqlUserRepository(s2)
        with pytest.raises(IntegrityError):
            await repo.get_or_create_by_sub(f"sub-b-{uuid4().hex[:6]}", shared_email)


async def test_user_get_or_create_by_sub_session_healthy_after_race_loss(db_session_factory):
    """A session that loses the insert race retains clean transaction state
    via savepoint rollback and can perform subsequent transactional work."""
    sub = f"sub-race-health-{uuid4().hex}"
    email = f"{sub}@example.com"

    # Pre-create user in s1
    async with db_session_factory() as s1:
        u1 = await SqlUserRepository(s1).get_or_create_by_sub(sub, email)
        await s1.commit()

    async with db_session_factory() as s2:
        user_repo = SqlUserRepository(s2)
        project_repo = SqlProjectRepository(s2)

        # In s2, get_or_create_by_sub returns the canonical user
        user = await user_repo.get_or_create_by_sub(sub, email)
        assert user.id == u1.id

        # Verify session can perform subsequent writes and commit cleanly
        proj = await project_repo.create(user.id, "Post-Race Project")
        await s2.commit()
        assert proj.id is not None
        assert proj.owner_id == user.id


# --- Projects ---------------------------------------------------------------


async def test_project_crud(repos, owner):
    p = await repos["projects"].create(owner.id, "Alpha")
    assert p.name == "Alpha" and p.revision == 1

    got = await repos["projects"].get(p.id, owner.id)
    assert got is not None and got.id == p.id

    # Wrong owner cannot read
    assert await repos["projects"].get(p.id, "no-such-owner") is None

    updated = await repos["projects"].update(p.id, owner.id, description="new desc")
    assert updated.revision == 2 and updated.description == "new desc"

    # Stale revision is rejected atomically (StaleRevisionError → 409 upstream)
    with pytest.raises(StaleRevisionError):
        await repos["projects"].update(p.id, owner.id, expected_revision=1, description="x")

    assert await repos["projects"].soft_delete(p.id, owner.id) is True
    assert await repos["projects"].get(p.id, owner.id) is None


async def test_project_get_by_name(repos, owner):
    await repos["projects"].create(owner.id, "Beta")
    assert (await repos["projects"].get_by_name(owner.id, "Beta")) is not None
    assert (await repos["projects"].get_by_name(owner.id, "missing")) is None


# --- Tasks ------------------------------------------------------------------


async def test_task_crud_with_project_link(repos, owner):
    p = await repos["projects"].create(owner.id, "TaskProj")
    t = await repos["tasks"].create(owner.id, "Do the thing", project_id=p.id, priority=2)
    assert t.status == "open" and t.priority == 2

    listed = await repos["tasks"].list_for_owner(owner.id, project_id=p.id)
    assert [x.id for x in listed] == [t.id]

    done = await repos["tasks"].update(t.id, owner.id, expected_revision=1, status="done")
    assert done.revision == 2 and done.status == "done"

    # optimistic lock blocks stale update atomically (409 upstream)
    with pytest.raises(StaleRevisionError):
        await repos["tasks"].update(t.id, owner.id, expected_revision=1, status="open")

    assert await repos["tasks"].soft_delete(t.id, owner.id) is True
    assert await repos["tasks"].get(t.id, owner.id) is None


async def test_task_and_project_mutations_append_revisioned_feed_items(db_session, repos, owner):
    project = await repos["projects"].create(owner.id, "Feed project")
    task = await repos["tasks"].create(
        owner.id, "Feed task", project_id=project.id, priority=2
    )
    task = await repos["tasks"].update(
        task.id, owner.id, expected_revision=1, status="done"
    )
    assert await repos["tasks"].soft_delete(task.id, owner.id)

    events = (
        await db_session.execute(
            select(SyncChangeEvent)
            .where(SyncChangeEvent.owner_id == owner.id)
            .order_by(SyncChangeEvent.sequence)
        )
    ).scalars().all()
    assert [(event.entity_type, event.revision) for event in events] == [
        ("project", 1),
        ("task", 1),
        ("task", 2),
        ("task", 3),
    ]
    assert events[1].snapshot["data"]["project_id"] == project.id
    assert events[2].snapshot["data"]["status"] == "done"
    assert events[3].snapshot is None
    assert events[3].tombstone["revision"] == 3
    assert await db_session.scalar(
        select(func.count()).select_from(SyncEntityRevision).where(
            SyncEntityRevision.owner_id == owner.id
        )
    ) == 4
    tombstone = await db_session.get(
        SyncTombstone, (owner.id, "task", task.id)
    )
    assert tombstone is not None and tombstone.revision == 3


async def test_entity_mutation_and_feed_event_roll_back_together(
    db_session, db_session_factory, repos, owner
):
    project = await repos["projects"].create(owner.id, "Rolled back project")
    assert await db_session.scalar(
        select(func.count()).select_from(SyncChangeEvent).where(
            SyncChangeEvent.entity_id == project.id
        )
    ) == 1
    await db_session.rollback()

    async with db_session_factory() as verify:
        assert await verify.scalar(
            select(func.count()).select_from(Project).where(Project.id == project.id)
        ) == 0
        assert await verify.scalar(
            select(func.count()).select_from(SyncChangeEvent).where(
                SyncChangeEvent.entity_id == project.id
            )
        ) == 0


# --- Captures ---------------------------------------------------------------


async def test_capture_idempotency_key_lookup(repos, owner):
    c1 = await repos["captures"].create(owner.id, "note", idempotency_key="key-1")
    found = await repos["captures"].find_by_idempotency_key(owner.id, "key-1")
    assert found.id == c1.id
    assert await repos["captures"].find_by_idempotency_key(owner.id, "other") is None


async def test_capture_status_update(repos, owner):
    c = await repos["captures"].create(owner.id, "note2")
    assert c.status == "pending"
    updated = await repos["captures"].update(c.id, owner.id, status="processed")
    assert updated.status == "processed" and updated.revision == 2


# --- Processing -------------------------------------------------------------


async def test_capture_processing_lifecycle(repos, owner):
    c = await repos["captures"].create(owner.id, "note3")
    proc = await repos["processing"].create(c.id)
    assert proc.status == "pending" and proc.attempt == 0

    proc = await repos["processing"].update(proc.id, status="processing", attempt=1)
    assert proc.status == "processing" and proc.attempt == 1

    from datetime import UTC, datetime

    proc = await repos["processing"].update(
        proc.id, status="succeeded", finished_at=datetime.now(UTC), result={"tasks": 2}
    )
    assert proc.status == "succeeded"
    assert proc.result == {"tasks": 2}
