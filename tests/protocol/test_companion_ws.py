"""Tests for /ws/companion WebSocket hub and JSON-RPC 2.0 broker (M2-B)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketDisconnect

from personal_os.api.routes.companion_ws import companion_websocket_endpoint
from personal_os.companion.protocol import PROTOCOL_VERSION, RPCErrorCode
from personal_os.db.models import User
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

    # Client-side helper methods
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
async def test_ws_handshake_success_and_heartbeat(
    api_app, proto_session: AsyncSession, test_user: User
):
    """Successful handshake transitions device to online and enables heartbeats."""
    service = DeviceService(
        proto_session, secret="test-secret-token-32-chars-long-123456"
    )
    device, token = await service.register_device(
        owner_id=test_user.id,
        device_id="dev_ws_alpha",
        name="WS Alpha",
        capabilities={"process.list": True, "filesystem.discover": True},
    )
    await proto_session.commit()

    async with open_companion_ws(api_app) as ws:
        # 1. Send auth.handshake
        handshake_payload = {
            "jsonrpc": "2.0",
            "id": "hs-1",
            "method": "auth.handshake",
            "params": {
                "protocol_version": PROTOCOL_VERSION,
                "device_id": "dev_ws_alpha",
                "token": token,
                "client_version": "0.1.0",
                "platform": "linux",
                "supported_capabilities": ["process.list", "filesystem.discover"],
            },
        }
        await ws.send_json(handshake_payload)
        resp = await ws.receive_json()

        assert resp["id"] == "hs-1"
        assert resp.get("error") is None
        assert resp["result"]["authenticated"] is True
        assert resp["result"]["device_id"] == "dev_ws_alpha"
        assert resp["result"]["user_id"] == test_user.id
        assert "process.list" in resp["result"]["granted_capabilities"]

        # 2. Send device.heartbeat
        hb_payload = {
            "jsonrpc": "2.0",
            "id": "hb-1",
            "method": "device.heartbeat",
            "params": {
                "device_id": "dev_ws_alpha",
                "status": "online",
            },
        }
        await ws.send_json(hb_payload)
        hb_resp = await ws.receive_json()
        assert hb_resp["id"] == "hb-1"
        assert hb_resp["result"]["acknowledged"] is True
        assert hb_resp["result"]["status"] == "online"

        # 3. Send system.ping
        await ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": "ping-1",
                "method": "system.ping",
                "params": {"echo": "test-ping"},
            }
        )
        ping_resp = await ws.receive_json()
        assert ping_resp["id"] == "ping-1"
        assert ping_resp["result"]["pong"] is True
        assert ping_resp["result"]["echo"] == "test-ping"


@pytest.mark.asyncio
async def test_ws_non_handshake_first_message_rejected(api_app):
    """Sending any RPC method before auth.handshake is rejected with -32001."""
    async with open_companion_ws(api_app) as ws:
        await ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "system.ping",
                "params": {},
            }
        )
        resp = await ws.receive_json()
        assert resp["error"] is not None
        assert resp["error"]["code"] == RPCErrorCode.UNAUTHORIZED


@pytest.mark.asyncio
async def test_ws_invalid_token_rejected(api_app):
    """Handshake with invalid token returns -32001 UNAUTHORIZED."""
    async with open_companion_ws(api_app) as ws:
        await ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": "hs-err",
                "method": "auth.handshake",
                "params": {
                    "protocol_version": PROTOCOL_VERSION,
                    "device_id": "dev_fake",
                    "token": "invalid-token",
                },
            }
        )
        resp = await ws.receive_json()
        assert resp["error"] is not None
        assert resp["error"]["code"] == RPCErrorCode.UNAUTHORIZED


@pytest.mark.asyncio
async def test_ws_protocol_version_mismatch(
    api_app, proto_session: AsyncSession, test_user: User
):
    """Handshake with incompatible protocol version returns -32004 VERSION_MISMATCH."""
    service = DeviceService(
        proto_session, secret="test-secret-token-32-chars-long-123456"
    )
    device, token = await service.register_device(
        owner_id=test_user.id,
        device_id="dev_ver_mismatch",
        name="Version Mismatch Rig",
    )
    await proto_session.commit()

    async with open_companion_ws(api_app) as ws:
        await ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": "hs-v",
                "method": "auth.handshake",
                "params": {
                    "protocol_version": "personal-os-rpc/v999",
                    "device_id": "dev_ver_mismatch",
                    "token": token,
                },
            }
        )
        resp = await ws.receive_json()
        assert resp["error"] is not None
        assert resp["error"]["code"] == RPCErrorCode.VERSION_MISMATCH


@pytest.mark.asyncio
async def test_ws_revoked_device_rejected(
    api_app, proto_session: AsyncSession, test_user: User
):
    """Handshake for a revoked device returns -32001 UNAUTHORIZED."""
    service = DeviceService(
        proto_session, secret="test-secret-token-32-chars-long-123456"
    )
    device, token = await service.register_device(
        owner_id=test_user.id,
        device_id="dev_revoked",
        name="Revoked Rig",
    )
    await service.revoke_device(device.device_id, owner_id=test_user.id)
    await proto_session.commit()

    async with open_companion_ws(api_app) as ws:
        await ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": "hs-rev",
                "method": "auth.handshake",
                "params": {
                    "protocol_version": PROTOCOL_VERSION,
                    "device_id": "dev_revoked",
                    "token": token,
                },
            }
        )
        resp = await ws.receive_json()
        assert resp["error"] is not None
        assert resp["error"]["code"] == RPCErrorCode.UNAUTHORIZED
        assert resp["error"]["data"].get("revoked") is True
