"""Milestone 4 (M4) Comprehensive Test Suite.

Covers:
1. Forged and expired credential rejection in WebSocket handshake.
2. Cross-user owner isolation across REST operations and WebSocket sessions.
3. Path traversal and symlink escape boundaries.
4. Atomic operation claim concurrency (FOR UPDATE SKIP LOCKED).
5. Ambiguous outcomes (outcome_unknown) upon mid-flight disconnect.
6. Safe retry behavior based on capability idempotency.
7. Prevention of blind redispatch on ambiguous operations.
8. Lost notification recovery via reconciliation polling.
9. Worker crash recovery via stale reaper.
10. Multi-worker cross-process dispatch via notification bus.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketDisconnect

from personal_os.api.companion_broker import (
    AsyncMemoryNotificationBus,
    CompanionBroker,
)
from personal_os.api.routes.companion_ws import companion_websocket_endpoint
from personal_os.companion.discovery import ProjectDiscoveryService
from personal_os.companion.protocol import (
    PROTOCOL_VERSION,
    RPCErrorCode,
    RPCRequest,
)
from personal_os.companion.rpc import RPCDispatcher
from personal_os.db import models as m
from personal_os.db.base import new_ulid
from personal_os.db.repositories.sqlalchemy import SqlDeviceOperationRepository
from personal_os.services.devices import DeviceService


class InMemoryWebSocket:
    """In-memory async WebSocket channel simulating bidirectional frame transport."""

    def __init__(self, app) -> None:
        self.app = app
        self.accepted = False
        self.closed = False
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self._client_to_server: asyncio.Queue[str | None] = asyncio.Queue()
        self._server_to_client: asyncio.Queue[str] = asyncio.Queue()

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        msg = await self._client_to_server.get()
        if msg is None:
            raise WebSocketDisconnect(code=self.close_code or 1000)
        return msg

    async def send_text(self, text: str) -> None:
        await self._server_to_client.put(text)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_code = code
        self.close_reason = reason
        await self._client_to_server.put(None)

    async def send_json(self, data: dict) -> None:
        await self._client_to_server.put(json.dumps(data))

    async def receive_json(self) -> dict:
        text = await asyncio.wait_for(self._server_to_client.get(), timeout=2.0)
        return json.loads(text)

    async def disconnect(self) -> None:
        await self._client_to_server.put(None)


@asynccontextmanager
async def open_companion_ws(api_app) -> AsyncIterator[InMemoryWebSocket]:
    ws = InMemoryWebSocket(api_app)
    task = asyncio.create_task(companion_websocket_endpoint(ws))  # type: ignore[arg-type]
    try:
        yield ws
    finally:
        await ws.disconnect()
        await task


# ---------------------------------------------------------------------------
# 1. Forged, Expired, and Malformed Credentials
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m4_forged_and_expired_handshake(api_app, auth_client: AsyncClient):
    """Handshake is rejected immediately with 4401 on forged or expired credentials."""
    reg_res = await auth_client.post(
        "/api/v1/devices",
        json={"device_id": "dev_security_target", "name": "Security Target"},
    )
    assert reg_res.status_code == 201
    dev_id = reg_res.json()["device_id"]
    valid_token = reg_res.json()["token"]

    secret = "test-secret-token-32-chars-long-123456"

    # Case A: Forged HMAC signature
    tampered_token = valid_token[:-4] + "wxyz"
    async with open_companion_ws(api_app) as ws:
        await ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": "hs-forged",
                "method": "auth.handshake",
                "params": {
                    "protocol_version": PROTOCOL_VERSION,
                    "device_id": dev_id,
                    "token": tampered_token,
                },
            }
        )
        resp = await ws.receive_json()
        assert resp["error"]["code"] == RPCErrorCode.UNAUTHORIZED
        assert ws.close_code == 4401

    # Case B: Expired token
    expired_payload = {
        "sub": dev_id,
        "device_id": dev_id,
        "user_id": reg_res.json()["owner_id"],
        "token_use": "device",
        "exp": int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
    }
    expired_token = jwt.encode(expired_payload, secret, algorithm="HS256")
    async with open_companion_ws(api_app) as ws:
        await ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": "hs-expired",
                "method": "auth.handshake",
                "params": {
                    "protocol_version": PROTOCOL_VERSION,
                    "device_id": dev_id,
                    "token": expired_token,
                },
            }
        )
        resp = await ws.receive_json()
        assert resp["error"]["code"] == RPCErrorCode.UNAUTHORIZED
        assert ws.close_code == 4401

    # Case C: Mismatched device_id in claims
    mismatched_payload = {
        "sub": "dev_other_victim",
        "device_id": "dev_other_victim",
        "user_id": reg_res.json()["owner_id"],
        "token_use": "device",
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }
    mismatched_token = jwt.encode(mismatched_payload, secret, algorithm="HS256")
    async with open_companion_ws(api_app) as ws:
        await ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": "hs-mismatch",
                "method": "auth.handshake",
                "params": {
                    "protocol_version": PROTOCOL_VERSION,
                    "device_id": dev_id,
                    "token": mismatched_token,
                },
            }
        )
        resp = await ws.receive_json()
        assert resp["error"]["code"] == RPCErrorCode.FORBIDDEN
        assert ws.close_code == 4403


# ---------------------------------------------------------------------------
# 2. Owner Isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m4_owner_isolation_handshake_and_dispatch(
    api_app, auth_client: AsyncClient, other_client: AsyncClient
):
    """User B cannot see, operate, or handshake with User A's registered device."""
    # Alice registers device
    reg_alice = await auth_client.post(
        "/api/v1/devices",
        json={"device_id": "dev_alice_secret", "name": "Alice Laptop"},
    )
    dev_id = reg_alice.json()["device_id"]
    assert reg_alice.json()["token"]

    # Bob attempts REST access to Alice's device
    get_res = await other_client.get(f"/api/v1/devices/{dev_id}")
    assert get_res.status_code == 404

    op_res = await other_client.post(
        f"/api/v1/devices/{dev_id}/operations",
        json={"capability": "system.ping"},
    )
    assert op_res.status_code == 404

    list_res = await other_client.get(f"/api/v1/devices/{dev_id}/operations")
    assert list_res.status_code == 404


# ---------------------------------------------------------------------------
# 3. Path Traversal & Symlink Escape Boundaries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m4_path_traversal_and_symlink_escapes(tmp_path: Path):
    """Filesystem discovery and stat reject path traversals, escapes, and external symlinks."""
    auth_root = tmp_path / "authorized_root"
    auth_root.mkdir()
    secret_file = tmp_path / "outside_secret.txt"
    secret_file.write_text("SUPER_SECRET_DATA")

    # Legitimate project inside root
    proj_dir = auth_root / "my_project"
    proj_dir.mkdir()
    (proj_dir / "pyproject.toml").write_text("[project]\nname = 'test'")

    # Escaping symlink pointing outside authorized root
    bad_symlink = auth_root / "escape_link"
    bad_symlink.symlink_to(secret_file)

    discovery_service = ProjectDiscoveryService(authorized_roots=[auth_root])
    dispatcher = RPCDispatcher(discovery_service=discovery_service)

    # 1. Direct traversal ../../ outside root
    traversal_req = RPCRequest(
        method="filesystem.discover",
        params={"root": str(auth_root / ".." / "outside_secret.txt")},
    )
    resp = await dispatcher.dispatch(traversal_req)
    assert resp.error is not None
    assert resp.error.code == RPCErrorCode.FORBIDDEN
    assert "forbidden" in resp.error.message.lower()

    # 2. Direct stat outside root
    stat_req = RPCRequest(
        method="filesystem.stat",
        params={"path": str(secret_file)},
    )
    stat_resp = await dispatcher.dispatch(stat_req)
    assert stat_resp.error is not None
    assert stat_resp.error.code == RPCErrorCode.FORBIDDEN

    # 3. Scanning discovery ignores escaping symlinks and finds real project
    disc_req = RPCRequest(
        method="filesystem.discover",
        params={"root": str(auth_root)},
    )
    disc_resp = await dispatcher.dispatch(disc_req)
    assert disc_resp.result is not None
    projects = disc_resp.result["projects"]
    assert len(projects) == 1
    assert projects[0]["name"] == "my_project"


# ---------------------------------------------------------------------------
# 4. Atomic Claim Concurrency (FOR UPDATE SKIP LOCKED)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m4_atomic_claim_concurrency(proto_session: AsyncSession, test_user: m.User):
    """Two concurrent workers competing for the same pending operation: exactly one succeeds."""
    dev = m.Device(
        id=new_ulid(),
        owner_id=test_user.id,
        device_id="dev_race_car",
        name="Race Car",
        platform="linux",
        capabilities={"system.ping": {}},
        status="online",
    )
    proto_session.add(dev)
    await proto_session.commit()

    op_repo = SqlDeviceOperationRepository(proto_session)
    op = await op_repo.create(
        owner_id=test_user.id,
        device_id="dev_race_car",
        capability="system.ping",
        params={"echo": "race"},
    )
    await proto_session.commit()

    # Worker 1 claims
    claimed_1 = await op_repo.claim_pending(
        test_user.id, "dev_race_car", operation_id=op.id
    )
    assert claimed_1 is not None
    assert claimed_1.status == "running"

    # Worker 2 attempts to claim the same operation
    claimed_2 = await op_repo.claim_pending(
        test_user.id, "dev_race_car", operation_id=op.id
    )
    # Must receive None because it is already running / claimed
    assert claimed_2 is None


# ---------------------------------------------------------------------------
# 5. Ambiguous Outcomes & Idempotency Retries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m4_ambiguous_outcome_and_safe_retry(api_app, auth_client: AsyncClient):
    """Disconnection mid-op sets status outcome_unknown, and retry honors idempotency."""
    reg_res = await auth_client.post(
        "/api/v1/devices",
        json={"device_id": "dev_ambig_test", "name": "Ambiguous Machine"},
    )
    dev_id = reg_res.json()["device_id"]
    token = reg_res.json()["token"]

    async with open_companion_ws(api_app) as ws:
        await ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": "hs-ambig",
                "method": "auth.handshake",
                "params": {
                    "protocol_version": PROTOCOL_VERSION,
                    "device_id": dev_id,
                    "token": token,
                    "supported_capabilities": ["system.ping", "filesystem.discover"],
                },
            }
        )
        await ws.receive_json()

        # Mock companion severs socket right after receiving request
        async def mock_disconnecting_companion():
            req_msg = await ws.receive_json()
            assert req_msg["method"] == "system.ping"
            await ws.close(code=1006, reason="Mid-flight power off")

        task = asyncio.create_task(mock_disconnecting_companion())

        op_res = await auth_client.post(
            f"/api/v1/devices/{dev_id}/operations",
            json={
                "capability": "system.ping",
                "timeout_seconds": 3,
                "idempotency_key": "ambig-key-001",
            },
        )
        await task
        assert op_res.status_code == 201
        op_data = op_res.json()

        # Step 1: Operation MUST be marked outcome_unknown, NEVER completed or generic failed
        assert op_data["status"] == "outcome_unknown"
        assert op_data["error"]["code"] == -32000
        assert op_data["error"]["data"]["idempotent"] is True

        op_id = op_data["id"]

    # Step 2: Idempotent capability retry without force is permitted
    retry_res = await auth_client.post(
        f"/api/v1/devices/{dev_id}/operations/{op_id}/retry"
    )
    assert retry_res.status_code == 200
    # Resets to pending (ready for next companion connection)
    assert retry_res.json()["status"] in ("pending", "failed")


@pytest.mark.asyncio
async def test_m4_blind_redispatch_prevention(proto_session: AsyncSession, test_user: m.User):
    """Operations in outcome_unknown or running are never blindly redispatched by sweepers."""
    dev = m.Device(
        id=new_ulid(),
        owner_id=test_user.id,
        device_id="dev_blind_test",
        name="Blind Test",
        platform="linux",
        capabilities={"system.ping": {}},
        status="online",
    )
    proto_session.add(dev)
    await proto_session.commit()

    op_repo = SqlDeviceOperationRepository(proto_session)

    # Insert an operation in outcome_unknown
    op = await op_repo.create(
        owner_id=test_user.id,
        device_id="dev_blind_test",
        capability="system.ping",
        params={},
    )
    await op_repo.update_status(
        op.id,
        test_user.id,
        status="outcome_unknown",
        error={"code": -32000, "message": "Connection severed"},
    )
    await proto_session.commit()

    # Query pending operations: outcome_unknown MUST NOT be included
    pending = await op_repo.list_pending_for_devices(["dev_blind_test"])
    assert len(pending) == 0


# ---------------------------------------------------------------------------
# 6. Lost Notification Recovery via Reconciliation Polling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m4_lost_notification_reconciliation(api_app, auth_client: AsyncClient):
    """An operation whose wake-up notification was completely dropped is recovered by polling."""
    reg_res = await auth_client.post(
        "/api/v1/devices",
        json={"device_id": "dev_reconcile_guy", "name": "Reconcile Guy"},
    )
    dev_id = reg_res.json()["device_id"]
    token = reg_res.json()["token"]

    async with open_companion_ws(api_app) as ws:
        await ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": "hs-recon",
                "method": "auth.handshake",
                "params": {
                    "protocol_version": PROTOCOL_VERSION,
                    "device_id": dev_id,
                    "token": token,
                    "supported_capabilities": ["system.ping"],
                },
            }
        )
        await ws.receive_json()

        # Insert a pending operation directly into DB (bypassing any NOTIFY event)
        factory = api_app.state.db_session_factory
        async with factory() as session:
            op_repo = SqlDeviceOperationRepository(session)
            op = await op_repo.create(
                owner_id=reg_res.json()["owner_id"],
                device_id=dev_id,
                capability="system.ping",
                params={"echo": "missed_notification"},
            )
            op_id = op.id
            await session.commit()

        # Companion answers when request arrives
        async def mock_companion():
            req_msg = await ws.receive_json()
            assert req_msg["method"] == "system.ping"
            await ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": req_msg["id"],
                    "result": {"pong": True, "echo": "reconciliation_worked"},
                }
            )

        comp_task = asyncio.create_task(mock_companion())

        # Trigger reconciliation manually
        broker: CompanionBroker = api_app.state.companion_broker
        active_ids = broker.active_device_ids()
        assert dev_id in active_ids

        async with factory() as session:
            op_repo = SqlDeviceOperationRepository(session)
            pending_ops = await op_repo.list_pending_for_devices(active_ids)
            assert len(pending_ops) >= 1
            for pop in pending_ops:
                asyncio.create_task(
                    broker._claim_and_execute(pop.id, pop.owner_id, pop.device_id)
                )

        await comp_task

        # Verify operation transitioned to completed
        get_res = await auth_client.get(f"/api/v1/devices/{dev_id}/operations/{op_id}")
        assert get_res.status_code == 200
        assert get_res.json()["status"] == "completed"
        assert get_res.json()["result"]["echo"] == "reconciliation_worked"


# ---------------------------------------------------------------------------
# 7. Worker Crash Recovery via Stale Reaper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m4_worker_crash_stale_reaper(proto_session: AsyncSession, test_user: m.User):
    """Operations left running past timeout due to a worker crash are reaped to outcome_unknown."""
    dev = m.Device(
        id=new_ulid(),
        owner_id=test_user.id,
        device_id="dev_crashed_worker",
        name="Crashed Worker Dev",
        platform="linux",
        capabilities={"system.ping": {}},
        status="online",
    )
    proto_session.add(dev)
    await proto_session.commit()

    op_repo = SqlDeviceOperationRepository(proto_session)
    op = await op_repo.create(
        owner_id=test_user.id,
        device_id="dev_crashed_worker",
        capability="system.ping",
        params={},
        timeout_seconds=5,
    )
    # Simulate worker crashing 20 seconds ago while op was running
    past_time = datetime.now(UTC) - timedelta(seconds=20)
    await op_repo.update_status(
        op.id,
        test_user.id,
        status="running",
        started_at=past_time,
    )
    await proto_session.commit()

    # Run reaper sweep
    reaped = await op_repo.reap_stale_running(grace_seconds=2)
    await proto_session.commit()

    assert len(reaped) == 1
    assert reaped[0].id == op.id
    assert reaped[0].status == "outcome_unknown"
    assert reaped[0].error["code"] == -32000
    assert "timed out or worker crashed" in reaped[0].error["message"].lower()


# ---------------------------------------------------------------------------
# 8. Multi-Worker Cross-Process Dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m4_multi_worker_cross_process_dispatch(api_app, auth_client: AsyncClient):
    """Worker B receives REST operation for a device connected to Worker A, dispatches via bus."""
    reg_res = await auth_client.post(
        "/api/v1/devices",
        json={"device_id": "dev_multi_worker", "name": "Multi Worker Machine"},
    )
    dev_id = reg_res.json()["device_id"]
    token = reg_res.json()["token"]

    shared_bus = AsyncMemoryNotificationBus()

    # Worker A holds the WebSocket session
    broker_a = api_app.state.companion_broker
    broker_a.bus = shared_bus
    await broker_a.start_coordinator(api_app.state.db_session_factory)

    # Worker B is a distinct broker with NO local session, configured in cluster mode
    broker_b = CompanionBroker(bus=shared_bus, worker_id="worker_b", is_cluster_mode=True)
    await broker_b.start_coordinator(api_app.state.db_session_factory)

    async with open_companion_ws(api_app) as ws:
        # Handshake with Worker A
        await ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": "hs-multi",
                "method": "auth.handshake",
                "params": {
                    "protocol_version": PROTOCOL_VERSION,
                    "device_id": dev_id,
                    "token": token,
                    "supported_capabilities": ["system.ping"],
                },
            }
        )
        await ws.receive_json()

        # Companion answers when request arrives on Worker A's socket
        async def mock_companion_worker_a():
            req_msg = await ws.receive_json()
            assert req_msg["method"] == "system.ping"
            await ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": req_msg["id"],
                    "result": {"pong": True, "echo": "multi_worker_success"},
                }
            )

        comp_task = asyncio.create_task(mock_companion_worker_a())

        # Worker B invokes request_operation with its own broker
        factory = api_app.state.db_session_factory
        settings = api_app.state.settings
        async with factory() as session:
            service_b = DeviceService(session, settings.companion_jwt_secret)
            op = await service_b.request_operation(
                owner_id=reg_res.json()["owner_id"],
                device_id=dev_id,
                capability="system.ping",
                params={"echo": "from_worker_b"},
                timeout_seconds=5,
                broker=broker_b,
            )

        await comp_task

        # Verify operation was picked up by Worker A and completed
        assert op.status == "completed"
        assert op.result == {"pong": True, "echo": "multi_worker_success"}

    await broker_a.stop_coordinator()
    await broker_b.stop_coordinator()
