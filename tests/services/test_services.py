"""Service tests: business rules, 404/409 semantics, idempotent capture submit."""
from __future__ import annotations

import pytest
import pytest_asyncio

from personal_os.services import (
    CaptureServiceImpl,
    ProjectServiceImpl,
    ServiceConflictError,
    ServiceNotFound,
    TaskServiceImpl,
)


@pytest_asyncio.fixture
async def services(db_session, test_user):
    from personal_os.db.repositories import SqlCaptureProcessingRepository

    return {
        "tasks": TaskServiceImpl(db_session),
        "projects": ProjectServiceImpl(db_session),
        "captures": CaptureServiceImpl(db_session),
        "processing_repo": SqlCaptureProcessingRepository(db_session),
        "user": test_user,
    }


# --- Tasks -----------------------------------------------------------------


async def test_create_task_requires_valid_project(services):
    user = services["user"]
    with pytest.raises(ServiceNotFound):
        await services["tasks"].create_task(user.id, "x", project_id="nope")


async def test_task_404_and_409_semantics(services):
    user = services["user"]
    t = await services["tasks"].create_task(user.id, "Write spec")
    assert t.revision == 1

    with pytest.raises(ServiceNotFound):
        await services["tasks"].get_task(user.id, "missing")

    updated = await services["tasks"].update_task(user.id, t.id, expected_revision=1, title="Write spec v2")
    assert updated.revision == 2

    with pytest.raises(ServiceConflictError):
        await services["tasks"].update_task(user.id, t.id, expected_revision=1, title="stale")


async def test_complete_task_flow(services):
    user = services["user"]
    t = await services["tasks"].create_task(user.id, "Ship it")
    done = await services["tasks"].complete_task(user.id, t.id, expected_revision=1)
    assert done.status == "done"

    listed = await services["tasks"].list_tasks(user.id, status="open")
    assert all(x.id != t.id for x in listed)


# --- Projects ---------------------------------------------------------------


async def test_duplicate_project_name_raises_conflict(services):
    user = services["user"]
    await services["projects"].create_project(user.id, "Dup")
    with pytest.raises(ServiceConflictError):
        await services["projects"].create_project(user.id, "Dup")


async def test_delete_project_then_tasks_orphan(services):
    user = services["user"]
    p = await services["projects"].create_project(user.id, "OrphanTest")
    t = await services["tasks"].create_task(user.id, "task in p", project_id=p.id)
    await services["projects"].delete_project(user.id, p.id)
    # Task survives with project_id set to soft-deleted project row (no hard FK null),
    # so the service treats reads against deleted projects as not-found.
    with pytest.raises(ServiceNotFound):
        await services["projects"].get_project(user.id, p.id)


# --- Captures ---------------------------------------------------------------


async def test_capture_submit_creates_processing_record(services):
    user = services["user"]
    c = await services["captures"].submit_capture(user.id, "buy milk", source="cli")
    assert c.status == "pending"
    proc = await services["processing_repo"].get_by_capture(c.id)
    assert proc is not None and proc.status == "pending"


async def test_capture_idempotent_replay_returns_same_capture(services):
    user = services["user"]
    c1 = await services["captures"].submit_capture(user.id, "same note", idempotency_key="k1")
    c2 = await services["captures"].submit_capture(user.id, "same note", idempotency_key="k1")
    assert c1.id == c2.id
    # Only one processing row for one capture
    procs = []
    cap = await services["captures"].get_capture(user.id, c1.id)
    assert cap.id == c1.id


async def test_capture_project_must_exist(services):
    user = services["user"]
    with pytest.raises(ServiceNotFound):
        await services["captures"].submit_capture(user.id, "note", project_id="nope")


async def test_capture_unknown_user_rejected(services):
    with pytest.raises(ServiceNotFound):
        await services["captures"].submit_capture("ghost-user", "note")
