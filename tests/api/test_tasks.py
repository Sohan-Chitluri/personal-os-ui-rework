"""Tests for Tasks API endpoints."""

from __future__ import annotations

from httpx import AsyncClient

from personal_os.sync.postgres_store import PostgresSyncStore


async def test_task_crud_lifecycle(auth_client: AsyncClient):
    """Test task creation, retrieval, listing, updating, completion, and deletion."""
    # 1. Create a project first
    proj_res = await auth_client.post(
        "/api/v1/projects",
        json={"name": "Task Test Project"},
    )
    assert proj_res.status_code == 201
    project_id = proj_res.json()["id"]

    # 2. Create a task linked to the project
    create_res = await auth_client.post(
        "/api/v1/tasks",
        json={
            "title": "Implement auth middleware",
            "project_id": project_id,
            "description": "Ensure owner scoping is enforced",
            "priority": 1,
        },
    )
    assert create_res.status_code == 201
    task = create_res.json()
    task_id = task["id"]
    assert task["title"] == "Implement auth middleware"
    assert task["project_id"] == project_id
    assert task["status"] == "open"
    assert task["priority"] == 1
    assert task["revision"] == 1

    # 3. Retrieve the task
    get_res = await auth_client.get(f"/api/v1/tasks/{task_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == task_id

    # 4. List tasks with filters
    list_res = await auth_client.get(
        "/api/v1/tasks",
        params={"project_id": project_id, "status": "open"},
    )
    assert list_res.status_code == 200
    items = list_res.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == task_id

    # 5. Update task with If-Match revision check
    update_res = await auth_client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"title": "Implement auth middleware v2"},
        headers={"If-Match": "1"},
    )
    assert update_res.status_code == 200
    updated = update_res.json()
    assert updated["title"] == "Implement auth middleware v2"
    assert updated["revision"] == 2

    # 6. Complete task
    complete_res = await auth_client.post(
        f"/api/v1/tasks/{task_id}/complete",
        headers={"If-Match": "2"},
    )
    assert complete_res.status_code == 200
    completed = complete_res.json()
    assert completed["status"] == "done"
    assert completed["revision"] == 3

    # Verify task is no longer in 'open' list
    open_list = await auth_client.get("/api/v1/tasks", params={"status": "open"})
    assert not any(t["id"] == task_id for t in open_list.json()["items"])

    # 7. Delete task
    del_res = await auth_client.delete(f"/api/v1/tasks/{task_id}")
    assert del_res.status_code == 204

    # 8. Verify 404 after deletion
    post_del = await auth_client.get(f"/api/v1/tasks/{task_id}")
    assert post_del.status_code == 404


async def test_task_revision_conflict(auth_client: AsyncClient):
    """Updating a task with a stale revision returns 409 Conflict."""
    create_res = await auth_client.post(
        "/api/v1/tasks",
        json={"title": "Revision Conflict Task"},
    )
    assert create_res.status_code == 201
    task_id = create_res.json()["id"]

    # Current revision is 1, expect 409 when passing If-Match: 99
    res = await auth_client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"title": "Stale update"},
        headers={"If-Match": "99"},
    )
    assert res.status_code == 409
    body = res.json()
    assert body["status"] == 409
    assert body["type"] == "https://api.pos-deku.com/errors/conflict"
    assert body["extensions"]["current_revision"] == 1


async def test_task_nonexistent_project_returns_404(auth_client: AsyncClient):
    """Creating a task linked to a non-existent project returns 404."""
    res = await auth_client.post(
        "/api/v1/tasks",
        json={"title": "Orphaned Task", "project_id": "nonexistent-project-id"},
    )
    assert res.status_code == 404
    body = res.json()
    assert body["status"] == 404
    assert body["type"] == "https://api.pos-deku.com/errors/not_found"


async def test_task_owner_scoping(auth_client: AsyncClient, other_client: AsyncClient):
    """User B cannot view, update, complete, or delete User A's task."""
    res = await auth_client.post(
        "/api/v1/tasks",
        json={"title": "User A Private Task"},
    )
    assert res.status_code == 201
    task_id = res.json()["id"]

    # User B attempts to get User A's task -> 404
    other_get = await other_client.get(f"/api/v1/tasks/{task_id}")
    assert other_get.status_code == 404

    # User B attempts to update User A's task -> 404
    other_update = await other_client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"title": "Hijacked"},
    )
    assert other_update.status_code == 404

    # User B attempts to complete User A's task -> 404
    other_complete = await other_client.post(f"/api/v1/tasks/{task_id}/complete")
    assert other_complete.status_code == 404

    # User B attempts to delete User A's task -> 404
    other_delete = await other_client.delete(f"/api/v1/tasks/{task_id}")
    assert other_delete.status_code == 404


async def test_task_payload_cannot_override_owner(auth_client: AsyncClient, other_user):
    """Even if a request body attempts to supply an owner_id,
    it is ignored and owner_id comes from auth."""
    res = await auth_client.post(
        "/api/v1/tasks",
        json={
            "title": "Spoof Test",
            "owner_id": other_user.id,  # Attempting to forge ownership
        },
    )
    assert res.status_code == 201
    task = res.json()
    assert task["owner_id"] != other_user.id


async def test_authenticated_task_and_project_mutations_reach_sync_feed(
    auth_client: AsyncClient, db_session, db_session_factory, test_user
):
    project_res = await auth_client.post(
        "/api/v1/projects", json={"name": "Feed-visible project"}
    )
    assert project_res.status_code == 201
    project_id = project_res.json()["id"]
    task_res = await auth_client.post(
        "/api/v1/tasks",
        json={"title": "Feed-visible task", "project_id": project_id},
    )
    assert task_res.status_code == 201
    task_id = task_res.json()["id"]

    query_res = await auth_client.get(f"/api/v1/tasks/{task_id}")
    assert query_res.status_code == 200
    assert query_res.json()["project_id"] == project_id

    # The API dependency shares one UoW for this test; commit before reading
    # through the isolated store's independent session factory.
    await db_session.commit()
    changes = await PostgresSyncStore(db_session_factory).list_changes(test_user.id)
    assert [(item.entity_type.value, item.revision) for item in changes] == [
        ("project", 1),
        ("task", 1),
    ]
    assert changes[1].snapshot is not None
    assert changes[1].snapshot.data["project_id"] == project_id


async def test_task_soft_and_hard_deadlines(auth_client: AsyncClient, db_session, db_session_factory, test_user):
    """Test soft and hard deadlines: creation, response serialization, updating, and sync feed."""
    soft = "2026-11-01T12:00:00Z"
    hard = "2026-11-05T18:00:00Z"

    # 1. Create with soft_deadline and hard_deadline
    create_res = await auth_client.post(
        "/api/v1/tasks",
        json={
            "title": "Dual deadline task",
            "soft_deadline": soft,
            "hard_deadline": hard,
        },
    )
    assert create_res.status_code == 201
    task = create_res.json()
    assert task["title"] == "Dual deadline task"
    assert task["due_date"] is not None
    assert task["soft_deadline"] is not None
    assert task["hard_deadline"] is not None

    task_id = task["id"]

    # 2. Get task
    get_res = await auth_client.get(f"/api/v1/tasks/{task_id}")
    assert get_res.status_code == 200
    got = get_res.json()
    assert got["soft_deadline"] is not None
    assert got["hard_deadline"] is not None
    assert got["due_date"] == got["soft_deadline"]

    # 3. Update hard_deadline only
    new_hard = "2026-11-08T20:00:00Z"
    patch_res = await auth_client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"hard_deadline": new_hard},
    )
    assert patch_res.status_code == 200
    patched = patch_res.json()
    assert patched["soft_deadline"] == got["soft_deadline"]

    # 4. Backward compatibility: Create task with due_date only
    legacy_res = await auth_client.post(
        "/api/v1/tasks",
        json={
            "title": "Legacy task",
            "due_date": "2026-12-01T00:00:00Z",
        },
    )
    assert legacy_res.status_code == 201
    legacy_task = legacy_res.json()
    assert legacy_task["due_date"] is not None
    assert legacy_task["soft_deadline"] == legacy_task["due_date"]
    assert legacy_task["hard_deadline"] is None

    # 5. Check sync feed includes hard_deadline in snapshot
    await db_session.commit()
    changes = await PostgresSyncStore(db_session_factory).list_changes(test_user.id)
    task_changes = [c for c in changes if c.entity_type.value == "task"]
    assert any("hard_deadline" in (c.snapshot.data if c.snapshot else {}) for c in task_changes)

