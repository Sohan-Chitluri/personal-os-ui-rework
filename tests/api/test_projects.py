"""Tests for Projects API endpoints."""

from __future__ import annotations

from httpx import AsyncClient


async def test_project_crud_lifecycle(auth_client: AsyncClient):
    """Test standard project creation, retrieval, update, and deletion."""
    # 1. Create project
    create_res = await auth_client.post(
        "/api/v1/projects",
        json={"name": "Alpha Project", "description": "Initial scope"},
    )
    assert create_res.status_code == 201
    project = create_res.json()
    project_id = project["id"]
    assert project["name"] == "Alpha Project"
    assert project["description"] == "Initial scope"
    assert project["revision"] == 1

    # 2. Get project
    get_res = await auth_client.get(f"/api/v1/projects/{project_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == project_id

    # 3. List projects
    list_res = await auth_client.get("/api/v1/projects")
    assert list_res.status_code == 200
    items = list_res.json()["items"]
    assert any(p["id"] == project_id for p in items)

    # 4. Update project with optimistic lock (If-Match: 1)
    update_res = await auth_client.patch(
        f"/api/v1/projects/{project_id}",
        json={"description": "Updated scope"},
        headers={"If-Match": "1"},
    )
    assert update_res.status_code == 200
    updated = update_res.json()
    assert updated["description"] == "Updated scope"
    assert updated["revision"] == 2

    # 5. Delete project
    del_res = await auth_client.delete(f"/api/v1/projects/{project_id}")
    assert del_res.status_code == 204

    # 6. Verify 404 after deletion
    post_del_res = await auth_client.get(f"/api/v1/projects/{project_id}")
    assert post_del_res.status_code == 404


async def test_project_name_duplicate_conflict(auth_client: AsyncClient):
    """Creating a project with duplicate name returns 409 Conflict."""
    res1 = await auth_client.post(
        "/api/v1/projects",
        json={"name": "Duplicate Project"},
    )
    assert res1.status_code == 201

    res2 = await auth_client.post(
        "/api/v1/projects",
        json={"name": "Duplicate Project"},
    )
    assert res2.status_code == 409
    body = res2.json()
    assert body["status"] == 409
    assert body["type"] == "https://api.pos-deku.com/errors/conflict"


async def test_project_revision_conflict(auth_client: AsyncClient):
    """Updating a project with stale revision returns 409 Conflict."""
    res = await auth_client.post(
        "/api/v1/projects",
        json={"name": "Conflict Project"},
    )
    assert res.status_code == 201
    project_id = res.json()["id"]

    # Provide stale revision (expected 99, current is 1)
    conflict_res = await auth_client.patch(
        f"/api/v1/projects/{project_id}",
        json={"description": "Stale update"},
        headers={"If-Match": "99"},
    )
    assert conflict_res.status_code == 409
    body = conflict_res.json()
    assert body["status"] == 409
    assert body["extensions"]["current_revision"] == 1


async def test_project_owner_scoping(auth_client: AsyncClient, other_client: AsyncClient):
    """User B cannot access or modify User A's project."""
    res = await auth_client.post(
        "/api/v1/projects",
        json={"name": "User A Private Project"},
    )
    assert res.status_code == 201
    project_id = res.json()["id"]

    # User B attempts to read User A's project -> 404
    other_get = await other_client.get(f"/api/v1/projects/{project_id}")
    assert other_get.status_code == 404

    # User B attempts to update User A's project -> 404
    other_update = await other_client.patch(
        f"/api/v1/projects/{project_id}",
        json={"name": "Hacked"},
    )
    assert other_update.status_code == 404

    # User B attempts to delete User A's project -> 404
    other_delete = await other_client.delete(f"/api/v1/projects/{project_id}")
    assert other_delete.status_code == 404

    # User B's list does not contain User A's project
    other_list = await other_client.get("/api/v1/projects")
    assert not any(p["id"] == project_id for p in other_list.json()["items"])


async def test_project_validation_failure(auth_client: AsyncClient):
    """Invalid payload schema returns 422 Unprocessable Entity."""
    res = await auth_client.post(
        "/api/v1/projects",
        json={"name": ""},  # min_length is 1
    )
    assert res.status_code == 422
    body = res.json()
    assert body["status"] == 422
    assert body["type"] == "https://api.pos-deku.com/errors/validation_error"
