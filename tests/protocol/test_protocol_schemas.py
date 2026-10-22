"""Tests for JSON-RPC 2.0 protocol models and framing (M2-B)."""

from datetime import UTC, datetime

from personal_os.companion.protocol import (
    DEFAULT_CAPABILITIES,
    PROTOCOL_VERSION,
    HandshakeParams,
    HandshakeResult,
    HeartbeatParams,
    HeartbeatResult,
    PresenceState,
    RPCErrorCode,
    RPCRequest,
    RPCResponse,
)


def test_rpc_request_and_response_envelopes():
    req = RPCRequest(method="process.list", params={"limit": 10}, id="req-1")
    json_str = req.model_dump_json()
    assert '"jsonrpc":"2.0"' in json_str
    assert '"method":"process.list"' in json_str
    assert '"id":"req-1"' in json_str

    success_resp = RPCResponse.success("req-1", {"ok": True})
    assert success_resp.error is None
    assert success_resp.result == {"ok": True}

    err_resp = RPCResponse.failure("req-1", RPCErrorCode.FORBIDDEN, "Access denied", {"path": "/etc"})
    assert err_resp.result is None
    assert err_resp.error is not None
    assert err_resp.error.code == -32002
    assert err_resp.error.message == "Access denied"
    assert err_resp.error.data == {"path": "/etc"}


def test_handshake_schemas():
    params = HandshakeParams(
        protocol_version=PROTOCOL_VERSION,
        device_id="dev_12345678",
        token="my-token",
        client_version="0.1.0",
        platform="linux",
        supported_capabilities=["process.list"],
    )
    assert params.device_id == "dev_12345678"
    assert params.protocol_version == "personal-os-rpc/v1"

    result = HandshakeResult(
        authenticated=True,
        session_id="sess_123",
        device_id="dev_12345678",
        user_id="usr_999",
        granted_capabilities=["process.list"],
    )
    assert result.authenticated is True
    assert result.heartbeat_interval_seconds == 15
    assert result.stale_timeout_seconds == 60


def test_heartbeat_schemas():
    now = datetime.now(UTC)
    params = HeartbeatParams(
        device_id="dev_123",
        timestamp=now,
        status=PresenceState.ONLINE,
    )
    assert params.status == PresenceState.ONLINE

    result = HeartbeatResult(
        acknowledged=True,
        server_timestamp=now,
        status=PresenceState.ONLINE,
    )
    assert result.acknowledged is True


def test_default_capabilities():
    assert "system.health" in DEFAULT_CAPABILITIES
    assert "process.list" in DEFAULT_CAPABILITIES
    assert "filesystem.discover" in DEFAULT_CAPABILITIES
    assert "filesystem.stat" in DEFAULT_CAPABILITIES

    # Ensure dangerous methods are NOT in default capabilities
    assert "terminal.control" not in DEFAULT_CAPABILITIES
    assert "process.exec" not in DEFAULT_CAPABILITIES
    assert "agent.orchestrate" not in DEFAULT_CAPABILITIES
