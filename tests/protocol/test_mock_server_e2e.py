"""End-to-end integration tests between CompanionDaemon and a mock WebSocket server.

Validates the full M2 lifecycle:
1. Outbound connection from daemon to cloud endpoint.
2. Handshake and capability negotiation.
3. Periodic heartbeat reporting and presence tracking.
4. Server-to-companion RPC execution (process.list, filesystem.discover, system.ping).
5. Clean disconnection and reconnect recovery.
6. Revocation handling and credential clearing.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import websockets

from personal_os.companion.config import CompanionSettings
from personal_os.companion.daemon import CompanionDaemon
from personal_os.companion.protocol import (
    DEFAULT_CAPABILITIES,
    PROTOCOL_VERSION,
    HandshakeResult,
    HeartbeatResult,
    PresenceState,
    RPCErrorCode,
    RPCRequest,
    RPCResponse,
)


@pytest.mark.asyncio
async def test_companion_mock_server_full_e2e(tmp_path: Path):
    """Full lifecycle: connection, handshake, heartbeat, RPC invocation, and clean shutdown."""
    # 1. Prepare local workspace with an authorized project root
    workspace_dir = tmp_path / "authorized_workspace"
    project_dir = workspace_dir / "my-python-app"
    project_dir.mkdir(parents=True)
    (project_dir / ".git").mkdir()
    (project_dir / "pyproject.toml").write_text("[project]\nname = 'my-python-app'\n")

    config_dir = tmp_path / "companion_config"
    config_dir.mkdir(parents=True, mode=0o700)

    # Pre-seed credentials so daemon has an auth token
    creds_file = config_dir / "credentials.json"
    creds_file.write_text(
        json.dumps(
            {
                "device_id": "dev_mock_e2e",
                "token": "device:dev_mock_e2e:user_alice",
                "registered_at": datetime.now(UTC).isoformat(),
            }
        )
    )
    creds_file.chmod(0o600)

    # Event signaling test completion phases
    handshake_done = asyncio.Event()
    heartbeat_received = asyncio.Event()
    rpc_tested = asyncio.Event()

    async def mock_server_handler(ws):
        # A. Await handshake request
        raw_msg = await ws.recv()
        data = json.loads(raw_msg)
        assert data.get("method") == "auth.handshake"
        params = data["params"]
        assert params["device_id"] == "dev_mock_e2e"
        assert params["protocol_version"] == PROTOCOL_VERSION

        # Reply with successful handshake
        result = HandshakeResult(
            authenticated=True,
            session_id="mock_sess_e2e_01",
            device_id="dev_mock_e2e",
            user_id="user_alice",
            granted_capabilities=list(DEFAULT_CAPABILITIES.keys()),
            heartbeat_interval_seconds=1,
        )
        resp = RPCResponse.success(data["id"], result.model_dump())
        await ws.send(resp.model_dump_json())
        handshake_done.set()

        # B. Await heartbeat
        hb_msg = await ws.recv()
        hb_data = json.loads(hb_msg)
        assert hb_data.get("method") == "device.heartbeat"
        assert hb_data["params"]["device_id"] == "dev_mock_e2e"
        assert hb_data["params"]["status"] == "online"

        hb_result = HeartbeatResult(
            acknowledged=True,
            status=PresenceState.ONLINE,
        )
        await ws.send(RPCResponse.success(hb_data["id"], hb_result.model_dump()).model_dump_json())
        heartbeat_received.set()

        # C. Server initiates process.list RPC on companion
        proc_req = RPCRequest(id="srv_req_1", method="process.list", params={"limit": 5})
        await ws.send(proc_req.model_dump_json())
        proc_resp_raw = await ws.recv()
        proc_resp = RPCResponse.model_validate(json.loads(proc_resp_raw))
        assert proc_resp.id == "srv_req_1"
        assert proc_resp.error is None
        assert isinstance(proc_resp.result, dict)
        assert "processes" in proc_resp.result
        assert isinstance(proc_resp.result["processes"], list)

        # D. Server initiates filesystem.discover RPC on companion
        fs_req = RPCRequest(id="srv_req_2", method="filesystem.discover", params={})
        await ws.send(fs_req.model_dump_json())
        fs_resp_raw = await ws.recv()
        fs_resp = RPCResponse.model_validate(json.loads(fs_resp_raw))
        assert fs_resp.id == "srv_req_2"
        assert fs_resp.error is None
        discovered = fs_resp.result["projects"]
        assert any(p["name"] == "my-python-app" for p in discovered)

        # E. Server initiates system.ping
        ping_req = RPCRequest(id="srv_req_3", method="system.ping", params={"echo": "e2e-ok"})
        await ws.send(ping_req.model_dump_json())
        ping_resp_raw = await ws.recv()
        ping_resp = RPCResponse.model_validate(json.loads(ping_resp_raw))
        assert ping_resp.id == "srv_req_3"
        assert ping_resp.result["pong"] is True
        assert ping_resp.result["echo"] == "e2e-ok"
        assert "timestamp" in ping_resp.result

        rpc_tested.set()

    # Start mock server on loopback with dynamic port
    async with websockets.serve(mock_server_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        settings = CompanionSettings(
            config_dir=config_dir,
            authorized_roots=[workspace_dir],
            ws_url=f"ws://127.0.0.1:{port}",
            heartbeat_interval_seconds=0.1,
            reconnect_min_backoff=0.05,
            reconnect_max_backoff=0.1,
            reconnect_jitter=0.0,
        )
        daemon = CompanionDaemon(settings)

        daemon_task = asyncio.create_task(daemon.start())

        try:
            # Wait for handshake, heartbeat, and RPC rounds to complete
            await asyncio.wait_for(handshake_done.wait(), timeout=3.0)
            await asyncio.wait_for(heartbeat_received.wait(), timeout=3.0)
            assert daemon.presence == PresenceState.ONLINE
            await asyncio.wait_for(rpc_tested.wait(), timeout=3.0)

        finally:
            await daemon.stop()
            daemon_task.cancel()
            await asyncio.gather(daemon_task, return_exceptions=True)

        assert not daemon.is_running
        assert daemon.presence == PresenceState.OFFLINE


@pytest.mark.asyncio
async def test_companion_reconnect_after_server_interruption(tmp_path: Path):
    """When connection is broken, daemon transitions offline and reconnects with backoff."""
    config_dir = tmp_path / "companion_config_reconn"
    config_dir.mkdir(parents=True, mode=0o700)
    creds_file = config_dir / "credentials.json"
    creds_file.write_text(
        json.dumps(
            {
                "device_id": "dev_reconnect_e2e",
                "token": "device:dev_reconnect_e2e:user_alice",
                "registered_at": datetime.now(UTC).isoformat(),
            }
        )
    )
    creds_file.chmod(0o600)

    connection_count = 0
    first_connected = asyncio.Event()
    second_connected = asyncio.Event()

    async def flapping_server(ws):
        nonlocal connection_count
        connection_count += 1

        raw = await ws.recv()
        data = json.loads(raw)
        assert data["method"] == "auth.handshake"

        result = HandshakeResult(
            authenticated=True,
            session_id=f"sess_{connection_count}",
            device_id="dev_reconnect_e2e",
            user_id="user_alice",
            granted_capabilities=list(DEFAULT_CAPABILITIES.keys()),
            heartbeat_interval_seconds=1,
        )
        await ws.send(RPCResponse.success(data["id"], result.model_dump()).model_dump_json())

        if connection_count == 1:
            first_connected.set()
            # Abruptly close to trigger reconnect
            await ws.close(code=1001, reason="Simulated server crash")
        else:
            second_connected.set()
            # Keep alive until test finishes
            try:
                await ws.wait_closed()
            except Exception:
                pass

    async with websockets.serve(flapping_server, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        settings = CompanionSettings(
            config_dir=config_dir,
            authorized_roots=[tmp_path],
            ws_url=f"ws://127.0.0.1:{port}",
            reconnect_min_backoff=0.05,
            reconnect_max_backoff=0.1,
            reconnect_jitter=0.0,
        )
        daemon = CompanionDaemon(settings)
        daemon_task = asyncio.create_task(daemon.start())

        try:
            await asyncio.wait_for(first_connected.wait(), timeout=3.0)
            await asyncio.wait_for(second_connected.wait(), timeout=3.0)
            for _ in range(20):
                if daemon.presence == PresenceState.ONLINE:
                    break
                await asyncio.sleep(0.02)
            assert connection_count >= 2
            assert daemon.presence == PresenceState.ONLINE
        finally:
            await daemon.stop()
            daemon_task.cancel()
            await asyncio.gather(daemon_task, return_exceptions=True)

        assert not daemon.is_running
        assert daemon.presence == PresenceState.OFFLINE


@pytest.mark.asyncio
async def test_companion_revoked_credentials_clears_local_state(tmp_path: Path):
    """When server returns revoked status during handshake, daemon wipes stored credentials."""
    config_dir = tmp_path / "companion_config"
    config_dir.mkdir(parents=True, mode=0o700)
    creds_file = config_dir / "credentials.json"
    creds_file.write_text(
        json.dumps(
            {
                "device_id": "dev_revoked_e2e",
                "token": "device:dev_revoked_e2e:user_alice",
                "registered_at": datetime.now(UTC).isoformat(),
            }
        )
    )
    creds_file.chmod(0o600)

    server_served = asyncio.Event()

    async def mock_revoking_server(ws):
        raw = await ws.recv()
        data = json.loads(raw)
        # Reply with UNAUTHORIZED + revoked=True
        resp = RPCResponse.failure(
            data["id"],
            RPCErrorCode.UNAUTHORIZED,
            "Device has been revoked by owner",
            {"revoked": True},
        )
        await ws.send(resp.model_dump_json())
        server_served.set()

    async with websockets.serve(mock_revoking_server, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        settings = CompanionSettings(
            config_dir=config_dir,
            authorized_roots=[tmp_path],
            ws_url=f"ws://127.0.0.1:{port}",
            reconnect_min_backoff=0.05,
            reconnect_max_backoff=0.1,
            reconnect_jitter=0.0,
        )
        daemon = CompanionDaemon(settings)
        daemon_task = asyncio.create_task(daemon.start())

        try:
            await asyncio.wait_for(server_served.wait(), timeout=3.0)
            # Give daemon a short moment to clear credentials on the revocation branch
            for _ in range(20):
                if not creds_file.exists():
                    break
                await asyncio.sleep(0.05)
        finally:
            await daemon.stop()
            daemon_task.cancel()
            await asyncio.gather(daemon_task, return_exceptions=True)

        # Verify local credentials file was wiped
        assert not creds_file.exists(), "Revocation did not clear stored credentials"
