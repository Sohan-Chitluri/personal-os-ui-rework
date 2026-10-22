"""Tests for device capability operations and end-to-end execution lifecycle (M2-E)."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import AsyncClient
from starlette.websockets import WebSocketDisconnect

from personal_os.api.routes.companion_ws import companion_websocket_endpoint
from personal_os.companion.protocol import PROTOCOL_VERSION


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




@pytest.mark.asyncio
async def test_device_operation_e2e_execution(api_app, auth_client: AsyncClient):
    """End-to-end: Register device -> WS handshake -> dispatch -> receive result -> persist."""
    # 1. Register device
    reg_res = await auth_client.post(
        "/api/v1/devices",
        json={
            "device_id": "dev_ops_alpha",
            "name": "Alpha Machine",
            "capabilities": {"system.ping": True, "filesystem.discover": True},
        },
    )
    assert reg_res.status_code == 201
    dev_data = reg_res.json()
    token = dev_data["token"]
    dev_id = dev_data["device_id"]

    async with open_companion_ws(api_app) as ws:
        # 2. Handshake over WS
        await ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": "hs-1",
                "method": "auth.handshake",
                "params": {
                    "protocol_version": PROTOCOL_VERSION,
                    "device_id": dev_id,
                    "token": token,
                    "supported_capabilities": ["system.ping", "filesystem.discover"],
                },
            }
        )
        hs_resp = await ws.receive_json()
        assert hs_resp.get("error") is None
        assert hs_resp["result"]["authenticated"] is True

        # 3. Simulate companion handling server RPC in background
        async def mock_companion_worker():
            req_msg = await ws.receive_json()
            assert req_msg["method"] == "system.ping"
            assert req_msg["params"]["echo"] == "ping-from-cloud"
            await ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": req_msg["id"],
                    "result": {"pong": True, "echo": "ping-from-cloud"},
                }
            )

        worker_task = asyncio.create_task(mock_companion_worker())

        # 4. Trigger operation from REST API
        op_res = await auth_client.post(
            f"/api/v1/devices/{dev_id}/operations",
            json={
                "capability": "system.ping",
                "params": {"echo": "ping-from-cloud"},
                "idempotency_key": "idem-op-001",
                "timeout_seconds": 5,
            },
        )
        await worker_task
        assert op_res.status_code == 201
        op_data = op_res.json()
        assert op_data["device_id"] == dev_id
        assert op_data["capability"] == "system.ping"
        assert op_data["status"] == "completed"
        assert op_data["result"] == {"pong": True, "echo": "ping-from-cloud"}
        assert op_data["error"] is None
        assert op_data["started_at"] is not None
        assert op_data["finished_at"] is not None

        # 5. Fetch operation by ID
        get_op = await auth_client.get(
            f"/api/v1/devices/{dev_id}/operations/{op_data['id']}"
        )
        assert get_op.status_code == 200
        assert get_op.json()["status"] == "completed"
        assert get_op.json()["result"] == {"pong": True, "echo": "ping-from-cloud"}

        # 6. List operations
        list_ops = await auth_client.get(f"/api/v1/devices/{dev_id}/operations")
        assert list_ops.status_code == 200
        assert list_ops.json()["total"] >= 1
        assert list_ops.json()["items"][0]["id"] == op_data["id"]


@pytest.mark.asyncio
async def test_device_operation_idempotent_replay(auth_client: AsyncClient):
    """Submitting with the same idempotency_key returns cached operation without re-execution."""
    reg_res = await auth_client.post(
        "/api/v1/devices",
        json={"device_id": "dev_idem_test", "name": "Idem Device"},
    )
    assert reg_res.status_code == 201
    dev_id = reg_res.json()["device_id"]

    # First request (device offline -> fails gracefully)
    res1 = await auth_client.post(
        f"/api/v1/devices/{dev_id}/operations",
        json={
            "capability": "system.ping",
            "params": {"echo": "test"},
            "idempotency_key": "key-fixed-12345",
        },
    )
    assert res1.status_code == 201
    data1 = res1.json()
    assert data1["status"] == "failed"
    assert data1["error"]["message"] == "Device is offline"

    # Second request with identical idempotency_key
    res2 = await auth_client.post(
        f"/api/v1/devices/{dev_id}/operations",
        json={
            "capability": "system.ping",
            "params": {"echo": "test"},
            "idempotency_key": "key-fixed-12345",
        },
    )
    assert res2.status_code == 201
    data2 = res2.json()
    # Must return exact same record
    assert data2["id"] == data1["id"]
    assert data2["status"] == data1["status"]


@pytest.mark.asyncio
async def test_device_operation_offline_device_fails_safely(auth_client: AsyncClient):
    """Dispatching to an offline device records failure and does not hang."""
    reg_res = await auth_client.post(
        "/api/v1/devices",
        json={"device_id": "dev_offline_guy", "name": "Offline Machine"},
    )
    dev_id = reg_res.json()["device_id"]

    res = await auth_client.post(
        f"/api/v1/devices/{dev_id}/operations",
        json={"capability": "system.ping", "params": {}},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "failed"
    assert data["error"]["code"] == -32001
    assert "offline" in data["error"]["message"].lower()


@pytest.mark.asyncio
async def test_device_operation_disallowed_capability_rejected(auth_client: AsyncClient):
    """Unknown or unapproved capability names return 400 Bad Request."""
    reg_res = await auth_client.post(
        "/api/v1/devices",
        json={"device_id": "dev_bad_cap", "name": "Test Rig"},
    )
    dev_id = reg_res.json()["device_id"]

    res = await auth_client.post(
        f"/api/v1/devices/{dev_id}/operations",
        json={"capability": "system.arbitrary_bash_exec", "params": {"cmd": "rm -rf /"}},
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_device_operation_cross_user_isolation(
    auth_client: AsyncClient, other_client: AsyncClient
):
    """User B cannot trigger or view operations on User A's device."""
    reg_res = await auth_client.post(
        "/api/v1/devices",
        json={"device_id": "dev_alice_private", "name": "Alice Laptop"},
    )
    dev_id = reg_res.json()["device_id"]

    # User B attempts to dispatch operation on Alice's device
    bad_op = await other_client.post(
        f"/api/v1/devices/{dev_id}/operations",
        json={"capability": "system.ping"},
    )
    assert bad_op.status_code == 404

    # User B attempts to list operations on Alice's device
    bad_list = await other_client.get(f"/api/v1/devices/{dev_id}/operations")
    assert bad_list.status_code == 404


@pytest.mark.asyncio
async def test_device_operation_timeout_behavior(api_app, auth_client: AsyncClient):
    """When companion hangs and exceeds deadline, operation transitions to timeout/failed."""
    reg_res = await auth_client.post(
        "/api/v1/devices",
        json={"device_id": "dev_slow_poke", "name": "Slow Machine"},
    )
    dev_id = reg_res.json()["device_id"]
    token = reg_res.json()["token"]

    async with open_companion_ws(api_app) as ws:
        # Handshake
        await ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": "hs-slow",
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

        # Companion receives request but intentionally NEVER replies
        async def mock_silent_companion():
            req_msg = await ws.receive_json()
            assert req_msg["method"] == "system.ping"
            # Hang forever until cancelled
            await asyncio.sleep(100.0)

        hang_task = asyncio.create_task(mock_silent_companion())

        try:
            # Trigger operation with very short 1s timeout
            op_res = await auth_client.post(
                f"/api/v1/devices/{dev_id}/operations",
                json={
                    "capability": "system.ping",
                    "timeout_seconds": 1,
                },
            )
            assert op_res.status_code == 201
            op_data = op_res.json()
            assert op_data["status"] == "timeout"
            assert op_data["result"] is None
            assert op_data["error"] is not None
            assert op_data["error"]["code"] == -32003
            assert "timed out" in op_data["error"]["message"].lower()
        finally:
            hang_task.cancel()
            await asyncio.gather(hang_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_device_operation_disconnect_mid_flight(api_app, auth_client: AsyncClient):
    """When socket drops mid-operation, operation fails safely and is never marked success."""
    reg_res = await auth_client.post(
        "/api/v1/devices",
        json={"device_id": "dev_dropper", "name": "Drop Machine"},
    )
    dev_id = reg_res.json()["device_id"]
    token = reg_res.json()["token"]

    async with open_companion_ws(api_app) as ws:
        # Handshake
        await ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": "hs-drop",
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

        # Companion receives request and immediately severs connection
        async def mock_crashing_companion():
            req_msg = await ws.receive_json()
            assert req_msg["method"] == "system.ping"
            await ws.close(code=1006, reason="Crash/Power loss")

        crash_task = asyncio.create_task(mock_crashing_companion())

        op_res = await auth_client.post(
            f"/api/v1/devices/{dev_id}/operations",
            json={
                "capability": "system.ping",
                "timeout_seconds": 3,
            },
        )
        await crash_task
        assert op_res.status_code == 201
        op_data = op_res.json()
        assert op_data["status"] == "outcome_unknown"
        assert op_data["result"] is None
        assert op_data["error"] is not None
        assert op_data["error"]["code"] == -32000
        assert (
            "disconnect" in op_data["error"]["message"].lower()
            or "terminated" in op_data["error"]["message"].lower()
            or "ambiguous" in op_data["error"]["message"].lower()
        )


