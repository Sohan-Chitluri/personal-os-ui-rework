"""PostgreSQL-backed tests for the authenticated synchronization API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update

from personal_os.api.deps import get_sync_store
from personal_os.db.base import new_ulid
from personal_os.db.models import Project, Task
from personal_os.sync.contracts import EntityType, OperationType, SyncOperation
from personal_os.sync.postgres_store import (
    PostgresSyncStore,
    SyncChangeEvent,
    SyncEntityRevision,
    SyncFeedCursor,
    SyncOperationReceipt,
)


@pytest_asyncio.fixture(autouse=True)
async def _bind_sync_store_to_isolated_postgres_schema(api_app, db_session_factory):
    api_app.dependency_overrides[get_sync_store] = lambda: PostgresSyncStore(
        db_session_factory
    )
    yield
    api_app.dependency_overrides.pop(get_sync_store, None)


def _operation(
    *,
    operation_type: OperationType,
    entity_id: str,
    base_revision: int | None,
    title: str | None = None,
    project_id: str | None = None,
    depends_on: tuple = (),
) -> SyncOperation:
    payload = None
    if operation_type != OperationType.DELETE:
        payload = {
            "title": title,
            "description": None,
            "status": "open",
            "priority": None,
            "due_date": None,
            "project_id": project_id,
            "capture_id": None,
        }
    return SyncOperation(
        operation_id=uuid4(),
        idempotency_key=f"sync-{uuid4()}",
        entity_type=EntityType.TASK,
        entity_id=entity_id,
        operation_type=operation_type,
        base_revision=base_revision,
        payload=payload,
        created_at=datetime.now(UTC),
        depends_on=depends_on,
    )


def _upload_body(*operations: SyncOperation) -> dict:
    return {
        "protocol_version": 1,
        "operations": [operation.model_dump(mode="json") for operation in operations],
    }


async def test_sync_upload_is_authenticated_owner_scoped_and_replay_safe(
    auth_client: AsyncClient, client: AsyncClient, db_session_factory, test_user
):
    operation = _operation(
        operation_type=OperationType.CREATE,
        entity_id=new_ulid(),
        base_revision=None,
        title="Uploaded task",
    )
    body = _upload_body(operation)

    unauthorized = await client.post("/api/v1/sync/operations", json=body)
    assert unauthorized.status_code == 401

    first = await auth_client.post("/api/v1/sync/operations", json=body)
    replay = await auth_client.post("/api/v1/sync/operations", json=body)
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["acknowledgements"][0]["status"] == "applied"

    async with db_session_factory() as session:
        task = await session.get(Task, operation.entity_id)
        assert task is not None and task.owner_id == test_user.id


async def test_sync_batch_returns_per_operation_success_and_stale_conflict(
    auth_client: AsyncClient,
    other_client: AsyncClient,
    db_session,
    db_session_factory,
):
    task_id = new_ulid()
    create = _operation(
        operation_type=OperationType.CREATE,
        entity_id=task_id,
        base_revision=None,
        title="Batch task",
    )
    stale_update = _operation(
        operation_type=OperationType.UPDATE,
        entity_id=task_id,
        base_revision=0,
        title="Stale version",
    )

    response = await auth_client.post(
        "/api/v1/sync/operations", json=_upload_body(create, stale_update)
    )
    assert response.status_code == 200
    acknowledgements = response.json()["acknowledgements"]
    assert [ack["status"] for ack in acknowledgements] == ["applied", "conflicted"]
    assert acknowledgements[1]["conflict_id"]
    conflict_id = acknowledgements[1]["conflict_id"]
    inspected = await auth_client.get(f"/api/v1/sync/conflicts/{conflict_id}")
    assert inspected.status_code == 200
    assert inspected.json()["local_payload"]["title"] == "Stale version"
    await db_session.commit()
    hidden = await other_client.get(f"/api/v1/sync/conflicts/{conflict_id}")
    assert hidden.status_code == 404
    async with db_session_factory() as session:
        task = await session.get(Task, task_id)
        assert task is not None and task.title == "Batch task" and task.revision == 1


async def test_sync_cross_owner_entities_associations_dependencies_and_cursors_are_hidden(
    auth_client: AsyncClient,
    other_client: AsyncClient,
    db_session,
    db_session_factory,
    test_user,
    other_user,
):
    created = _operation(
        operation_type=OperationType.CREATE,
        entity_id=new_ulid(),
        base_revision=None,
        title="Private task",
    )
    assert (
        await auth_client.post("/api/v1/sync/operations", json=_upload_body(created))
    ).status_code == 200
    await db_session.commit()
    first_page = await auth_client.get("/api/v1/sync/changes")
    assert first_page.status_code == 200
    alice_cursor = first_page.json()["next_cursor"]

    foreign_update = _operation(
        operation_type=OperationType.UPDATE,
        entity_id=created.entity_id,
        base_revision=1,
        title="Attempted cross-owner update",
    )
    response = await other_client.post(
        "/api/v1/sync/operations", json=_upload_body(foreign_update)
    )
    assert response.status_code == 200
    assert response.json()["acknowledgements"][0]["error_category"] == "missing_entity"

    foreign_create = _operation(
        operation_type=OperationType.CREATE,
        entity_id=created.entity_id,
        base_revision=None,
        title="Conflicting owner identity",
    )
    response = await other_client.post(
        "/api/v1/sync/operations", json=_upload_body(foreign_create)
    )
    assert response.status_code == 200
    assert response.json()["acknowledgements"][0]["status"] == "permanent"
    assert response.json()["acknowledgements"][0]["error_category"] == "invalid_operation"

    foreign_dependency = _operation(
        operation_type=OperationType.CREATE,
        entity_id=new_ulid(),
        base_revision=None,
        title="Depends on Alice's operation",
        depends_on=(created.operation_id,),
    )
    response = await other_client.post(
        "/api/v1/sync/operations", json=_upload_body(foreign_dependency)
    )
    assert response.status_code == 200
    assert response.json()["acknowledgements"][0]["status"] == "retryable"
    assert response.json()["acknowledgements"][0]["error_category"] == "dependency_pending"

    owner_project = await other_client.post(
        "/api/v1/projects", json={"name": "Foreign project"}
    )
    assert owner_project.status_code == 201
    await db_session.commit()
    invalid_association = _operation(
        operation_type=OperationType.CREATE,
        entity_id=new_ulid(),
        base_revision=None,
        title="Invalid association",
        project_id=owner_project.json()["id"],
    )
    response = await auth_client.post(
        "/api/v1/sync/operations", json=_upload_body(invalid_association)
    )
    assert response.status_code == 200
    assert response.json()["acknowledgements"][0]["status"] == "permanent"
    assert response.json()["acknowledgements"][0]["error_category"] == "invalid_operation"

    cross_owner_cursor = await other_client.get(
        "/api/v1/sync/changes", params={"cursor": alice_cursor}
    )
    assert cross_owner_cursor.status_code == 400
    own_feed = await other_client.get("/api/v1/sync/changes")
    assert own_feed.status_code == 200
    assert all(item["entity_id"] != created.entity_id for item in own_feed.json()["items"])

    async with db_session_factory() as session:
        alice_task = await session.get(Task, created.entity_id)
        assert alice_task is not None and alice_task.owner_id == test_user.id
        assert other_user.id != test_user.id


async def test_sync_delete_is_returned_as_revisioned_tombstone(
    auth_client: AsyncClient, test_user
):
    task_id = new_ulid()
    create = _operation(
        operation_type=OperationType.CREATE,
        entity_id=task_id,
        base_revision=None,
        title="Delete me",
    )
    delete = _operation(
        operation_type=OperationType.DELETE,
        entity_id=task_id,
        base_revision=1,
    )
    created = await auth_client.post("/api/v1/sync/operations", json=_upload_body(create))
    deleted = await auth_client.post("/api/v1/sync/operations", json=_upload_body(delete))
    assert created.json()["acknowledgements"][0]["revision"] == 1
    assert deleted.json()["acknowledgements"][0]["revision"] == 2

    page = await auth_client.get("/api/v1/sync/changes")
    assert page.status_code == 200
    deletion_item = page.json()["items"][-1]
    assert deletion_item["entity_id"] == task_id
    assert deletion_item["revision"] == 2
    assert deletion_item["snapshot"] is None
    assert deletion_item["tombstone"]["entity_id"] == task_id
    assert deletion_item["tombstone"]["revision"] == 2
    assert deletion_item["tombstone"]["sequence"] == deletion_item["sequence"]


async def test_sync_feed_paginates_in_order_and_recovers_expired_or_invalid_cursor(
    auth_client: AsyncClient,
    db_session_factory,
    test_user,
):
    operations = [
        _operation(
            operation_type=OperationType.CREATE,
            entity_id=new_ulid(),
            base_revision=None,
            title=f"Page task {index}",
        )
        for index in range(3)
    ]
    uploaded = await auth_client.post(
        "/api/v1/sync/operations", json=_upload_body(*operations)
    )
    assert uploaded.status_code == 200

    first = await auth_client.get("/api/v1/sync/changes", params={"limit": 2})
    assert first.status_code == 200
    assert len(first.json()["items"]) == 2 and first.json()["has_more"] is True
    second = await auth_client.get(
        "/api/v1/sync/changes",
        params={"cursor": first.json()["next_cursor"], "limit": 2},
    )
    assert second.status_code == 200
    assert len(second.json()["items"]) == 1 and second.json()["has_more"] is False
    sequences = [item["sequence"] for item in first.json()["items"] + second.json()["items"]]
    assert sequences == sorted(sequences) and len(set(sequences)) == 3

    cursor = second.json()["next_cursor"]
    from hashlib import sha256

    digest = sha256(cursor.encode("ascii")).hexdigest()
    async with db_session_factory() as session, session.begin():
        await session.execute(
            update(SyncFeedCursor)
            .where(SyncFeedCursor.token_hash == digest, SyncFeedCursor.owner_id == test_user.id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    expired = await auth_client.get("/api/v1/sync/changes", params={"cursor": cursor})
    assert expired.status_code == 410
    assert expired.json()["extensions"]["code"] == "cursor_expired"

    malformed = await auth_client.get(
        "/api/v1/sync/changes", params={"cursor": "unknown-cursor-value-which-is-long-enough"}
    )
    assert malformed.status_code == 400
    assert malformed.json()["extensions"]["code"] == "invalid_cursor"

    recovered = await auth_client.get("/api/v1/sync/changes", params={"limit": 10})
    assert recovered.status_code == 200
    assert [item["sequence"] for item in recovered.json()["items"]] == sequences


async def test_sync_rejects_invalid_envelopes_and_protocol_before_applying_batch(
    auth_client: AsyncClient, db_session_factory, test_user
):
    create = _operation(
        operation_type=OperationType.CREATE,
        entity_id=new_ulid(),
        base_revision=None,
        title="Must not be applied",
    )
    valid = create.model_dump(mode="json")
    malformed = {"operation_id": str(uuid4()), "protocol_version": 1}
    invalid = await auth_client.post(
        "/api/v1/sync/operations",
        json={"protocol_version": 1, "operations": [valid, malformed]},
    )
    assert invalid.status_code == 422
    assert invalid.json()["extensions"]["code"] == "invalid_operation"

    mismatch = await auth_client.post(
        "/api/v1/sync/operations",
        json={"protocol_version": 2, "operations": [valid]},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["extensions"]["code"] == "protocol_mismatch"

    async with db_session_factory() as session:
        assert await session.get(Task, create.entity_id) is None
        assert await session.scalar(
            select(func.count()).select_from(SyncOperationReceipt).where(
                SyncOperationReceipt.owner_id == test_user.id
            )
        ) == 0


async def test_sync_upload_transaction_rolls_back_entity_and_metadata_on_failure(
    api_app,
    auth_headers,
    db_session_factory,
    test_user,
    monkeypatch: pytest.MonkeyPatch,
):
    operation = _operation(
        operation_type=OperationType.CREATE,
        entity_id=new_ulid(),
        base_revision=None,
        title="Rollback task",
    )

    async def fail_append(*args, **kwargs):
        raise RuntimeError("simulated feed storage failure")

    monkeypatch.setattr(PostgresSyncStore, "_append_change", fail_append)
    async with AsyncClient(
        transport=ASGITransport(app=api_app, raise_app_exceptions=False),
        base_url="http://testserver",
        headers=auth_headers,
    ) as client:
        response = await client.post(
            "/api/v1/sync/operations", json=_upload_body(operation)
        )
    assert response.status_code == 500

    async with db_session_factory() as session:
        assert await session.get(Task, operation.entity_id) is None
        assert await session.scalar(
            select(func.count()).select_from(SyncOperationReceipt).where(
                SyncOperationReceipt.operation_id == operation.operation_id
            )
        ) == 0
        assert await session.scalar(
            select(func.count()).select_from(SyncEntityRevision).where(
                SyncEntityRevision.entity_id == operation.entity_id
            )
        ) == 0
        assert await session.scalar(
            select(func.count()).select_from(SyncChangeEvent).where(
                SyncChangeEvent.entity_id == operation.entity_id
            )
        ) == 0


async def test_e2e_ui_queued_mutations_persist_to_canonical_postgresql(
    auth_client: AsyncClient,
    db_session_factory,
    test_user,
):
    project_id = new_ulid()
    project_op_id = uuid4()
    project_create = SyncOperation(
        operation_id=project_op_id,
        idempotency_key=f"project-create-{project_id}",
        entity_type=EntityType.PROJECT,
        entity_id=project_id,
        operation_type=OperationType.CREATE,
        base_revision=None,
        payload={"name": "UI Project Alpha", "description": None, "status": "active"},
        created_at=datetime.now(UTC),
        depends_on=(),
    )

    task_id = new_ulid()
    task_op_id = uuid4()
    task_create = SyncOperation(
        operation_id=task_op_id,
        idempotency_key=f"task-create-{task_id}",
        entity_type=EntityType.TASK,
        entity_id=task_id,
        operation_type=OperationType.CREATE,
        base_revision=None,
        payload={
            "title": "UI Task Alpha",
            "description": "[mood:Deep] [estimate:30]",
            "status": "open",
            "priority": 3,
            "due_date": None,
            "project_id": project_id,
            "capture_id": None,
        },
        created_at=datetime.now(UTC),
        depends_on=(project_op_id,),
    )

    # 1. Upload both operations (project first, task dependent on project)
    response = await auth_client.post(
        "/api/v1/sync/operations", json=_upload_body(project_create, task_create)
    )
    assert response.status_code == 200
    acks = response.json()["acknowledgements"]
    assert len(acks) == 2
    assert acks[0]["status"] == "applied" and acks[0]["revision"] == 1
    assert acks[1]["status"] == "applied" and acks[1]["revision"] == 1

    # 2. Verify rows in PostgreSQL
    async with db_session_factory() as session:
        proj = await session.get(Project, project_id)
        assert proj is not None
        assert proj.name == "UI Project Alpha"
        assert proj.owner_id == test_user.id
        assert proj.revision == 1

        task = await session.get(Task, task_id)
        assert task is not None
        assert task.title == "UI Task Alpha"
        assert task.project_id == project_id
        assert task.owner_id == test_user.id
        assert task.revision == 1

    # 3. Offline UI update: user updates task title and completes task
    task_update_op_id = uuid4()
    task_update = SyncOperation(
        operation_id=task_update_op_id,
        idempotency_key=f"task-update-{task_id}",
        entity_type=EntityType.TASK,
        entity_id=task_id,
        operation_type=OperationType.UPDATE,
        base_revision=1,
        payload={
            "title": "UI Task Alpha Done",
            "description": "[mood:Deep] [estimate:30]",
            "status": "done",
            "priority": 3,
            "due_date": None,
            "project_id": project_id,
            "capture_id": None,
        },
        created_at=datetime.now(UTC),
        depends_on=(),
    )

    response2 = await auth_client.post(
        "/api/v1/sync/operations", json=_upload_body(task_update)
    )
    assert response2.status_code == 200
    acks2 = response2.json()["acknowledgements"]
    assert acks2[0]["status"] == "applied" and acks2[0]["revision"] == 2

    # Verify updated row in PostgreSQL
    async with db_session_factory() as session:
        task2 = await session.get(Task, task_id)
        assert task2 is not None
        assert task2.title == "UI Task Alpha Done"
        assert task2.status == "done"
        assert task2.revision == 2

    # 4. Offline UI delete: user deletes task
    task_delete_op_id = uuid4()
    task_delete = SyncOperation(
        operation_id=task_delete_op_id,
        idempotency_key=f"task-delete-{task_id}",
        entity_type=EntityType.TASK,
        entity_id=task_id,
        operation_type=OperationType.DELETE,
        base_revision=2,
        payload=None,
        created_at=datetime.now(UTC),
        depends_on=(),
    )

    response3 = await auth_client.post(
        "/api/v1/sync/operations", json=_upload_body(task_delete)
    )
    assert response3.status_code == 200
    acks3 = response3.json()["acknowledgements"]
    assert acks3[0]["status"] == "applied"

    # Verify deletion in PostgreSQL
    async with db_session_factory() as session:
        task3 = await session.get(Task, task_id)
        assert task3 is None or getattr(task3, "deleted_at", None) is not None

