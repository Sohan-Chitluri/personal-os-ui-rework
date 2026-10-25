"""Tests for Captures and Processing Status API endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from personal_os.api.deps import get_capture_queue
from personal_os.api.main import create_app
from personal_os.config import Settings
from personal_os.db.models import CaptureDispatchOutbox
from personal_os.services.interfaces import QueueDispatchError
from personal_os.services.outbox import CaptureOutboxDispatcher
from personal_os.services.queue import LocalCaptureQueue


async def test_capture_submit_lifecycle(auth_client: AsyncClient):
    """Test standard capture submission and processing status retrieval."""
    # 1. Submit capture
    res = await auth_client.post(
        "/api/v1/captures",
        json={"content": "Pick up groceries and file expense report", "source": "web"},
    )
    assert res.status_code == 201
    receipt = res.json()
    assert receipt["persisted"] is True
    capture_id = receipt["capture_id"]

    # 2. Retrieve capture details (enriched with processing status)
    get_res = await auth_client.get(f"/api/v1/captures/{capture_id}")
    assert get_res.status_code == 200
    detail = get_res.json()
    assert detail["id"] == capture_id
    assert detail["content"] == "Pick up groceries and file expense report"
    assert detail["processing_status"] == "pending"
    assert detail["processing_job_id"] is not None
    job_id = detail["processing_job_id"]

    # 3. Retrieve status via /api/v1/captures/{id}/status
    status_res = await auth_client.get(f"/api/v1/captures/{capture_id}/status")
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["capture_id"] == capture_id
    assert status_data["status"] == "pending"
    assert status_data["attempt"] == 0

    # 4. Retrieve status via /api/v1/processing/{job_id}
    job_res = await auth_client.get(f"/api/v1/processing/{job_id}")
    assert job_res.status_code == 200
    job_data = job_res.json()
    assert job_data["id"] == job_id
    assert job_data["capture_id"] == capture_id

    # 5. List captures
    list_res = await auth_client.get("/api/v1/captures")
    assert list_res.status_code == 200
    items = list_res.json()["items"]
    assert any(c["id"] == capture_id for c in items)

    # 6. Delete capture
    del_res = await auth_client.delete(f"/api/v1/captures/{capture_id}")
    assert del_res.status_code == 204

    # 7. Verify 404 after deletion
    post_del = await auth_client.get(f"/api/v1/captures/{capture_id}")
    assert post_del.status_code == 404


async def test_lifespan_dispatcher_publishes_submitted_capture(
    api_app, auth_client: AsyncClient, db_session
):
    response = await auth_client.post("/api/v1/captures", json={"content": "queue dispatch"})
    assert response.status_code == 201
    assert api_app.state.capture_queue.size == 0
    await db_session.commit()
    factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False, autoflush=False)
    dispatcher = CaptureOutboxDispatcher(factory, api_app.state.capture_queue)
    assert await dispatcher.run_once() == 1
    assert api_app.state.capture_queue.size == 1


async def test_missing_production_queue_configuration_fails_startup():
    app = create_app(
        Settings(
            environment="staging",
            cognito_user_pool_id="pool",
            cognito_app_client_id="client",
            companion_jwt_secret="secret",
            queue_backend="inline",
        )
    )
    with pytest.raises(RuntimeError, match="supported only in local/test"):
        async with app.router.lifespan_context(app):
            pass


async def test_local_queue_is_created_once_for_application_lifespan():
    app = create_app(Settings(environment="test"))
    async with app.router.lifespan_context(app):
        queue = app.state.capture_queue
        await queue.enqueue("capture-1", "owner-1")
        assert app.state.capture_queue is queue
        assert queue.size == 1


async def test_capture_raw_text_alias_support(auth_client: AsyncClient):
    """Contract compatibility: client sending 'raw_text' is accepted."""
    res = await auth_client.post(
        "/api/v1/captures",
        json={"raw_text": "Buy battery backup", "source": "CLI"},
    )
    assert res.status_code == 201
    receipt = res.json()
    assert receipt["persisted"] is True


async def test_capture_idempotent_submission(auth_client: AsyncClient):
    """Duplicate submissions with same idempotency_key return the same capture."""
    idem_key = "test-idem-key-12345"

    res1 = await auth_client.post(
        "/api/v1/captures",
        json={"content": "Important note to deduplicate", "idempotency_key": idem_key},
    )
    assert res1.status_code == 201
    cap1_id = res1.json()["capture_id"]

    res2 = await auth_client.post(
        "/api/v1/captures",
        json={"content": "Important note to deduplicate", "idempotency_key": idem_key},
    )
    assert res2.status_code in (200, 201)
    cap2_id = res2.json()["capture_id"]
    assert cap1_id == cap2_id


async def test_capture_dispatch_failure_resilience(
    api_app, auth_client: AsyncClient, db_session
):
    """A failed outbox send leaves the committed capture eligible for retry."""

    def _broken_enqueue(capture_id: str, owner_id: str):
        raise QueueDispatchError("SQS outage simulated")

    broken_queue = LocalCaptureQueue(fail_enqueue=_broken_enqueue)
    api_app.dependency_overrides[get_capture_queue] = lambda: broken_queue

    try:
        res = await auth_client.post(
            "/api/v1/captures",
            json={"content": "Capture during network failure"},
        )
        assert res.status_code == 201
        await db_session.commit()
        factory = async_sessionmaker(
            bind=db_session.bind, expire_on_commit=False, autoflush=False
        )
        dispatcher = CaptureOutboxDispatcher(factory, broken_queue, retry_base_seconds=1)
        assert await dispatcher.run_once() == 0
        row = await db_session.scalar(
            select(CaptureDispatchOutbox).where(
                CaptureDispatchOutbox.capture_id == res.json()["capture_id"]
            )
        )
        await db_session.refresh(row)
        assert row.state == "pending"
        assert row.attempts == 1 and row.last_error == "QueueDispatchError"
    finally:
        del api_app.dependency_overrides[get_capture_queue]


async def test_capture_owner_scoping(auth_client: AsyncClient, other_client: AsyncClient):
    """User B cannot view or delete User A's capture or processing job."""
    res = await auth_client.post(
        "/api/v1/captures",
        json={"content": "User A confidential capture"},
    )
    assert res.status_code == 201
    capture_id = res.json()["capture_id"]

    # Get job_id for User A's capture
    detail_res = await auth_client.get(f"/api/v1/captures/{capture_id}")
    job_id = detail_res.json()["processing_job_id"]

    # User B attempts to read capture -> 404
    other_cap_get = await other_client.get(f"/api/v1/captures/{capture_id}")
    assert other_cap_get.status_code == 404

    # User B attempts to read capture status -> 404
    other_status_get = await other_client.get(f"/api/v1/captures/{capture_id}/status")
    assert other_status_get.status_code == 404

    # User B attempts to read processing job -> 404
    other_job_get = await other_client.get(f"/api/v1/processing/{job_id}")
    assert other_job_get.status_code == 404

    # User B attempts to delete capture -> 404
    other_del = await other_client.delete(f"/api/v1/captures/{capture_id}")
    assert other_del.status_code == 404


async def test_capture_list_pagination_and_total_count(auth_client: AsyncClient):
    """Capture list endpoint reports true total matching count across multiple pages and filters."""
    # Submit 5 captures
    for i in range(5):
        resp = await auth_client.post(
            "/api/v1/captures",
            json={"content": f"Pagination test note {i}"},
        )
        assert resp.status_code == 201

    # Page 1: limit 2, offset 0
    p1 = await auth_client.get("/api/v1/captures?limit=2&offset=0")
    assert p1.status_code == 200
    data1 = p1.json()
    assert len(data1["items"]) == 2
    assert data1["total"] >= 5

    # Page 2: limit 2, offset 2
    p2 = await auth_client.get("/api/v1/captures?limit=2&offset=2")
    assert p2.status_code == 200
    data2 = p2.json()
    assert len(data2["items"]) == 2
    assert data2["total"] == data1["total"]

    # Filter with no matching records returns total=0 and items=[]
    p_none = await auth_client.get("/api/v1/captures?status=nonexistent_status_12345")
    assert p_none.status_code == 200
    data_none = p_none.json()
    assert data_none["items"] == []
    assert data_none["total"] == 0
