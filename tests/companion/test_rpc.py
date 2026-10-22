"""Tests for RPCDispatcher (M2-A)."""

from pathlib import Path

import pytest

from personal_os.companion.discovery import ProjectDiscoveryService
from personal_os.companion.protocol import RPCErrorCode, RPCRequest
from personal_os.companion.rpc import RPCDispatcher


@pytest.fixture
def rpc_dispatcher(tmp_path: Path):
    root = tmp_path / "authorized"
    root.mkdir()
    p1 = root / "my_project"
    p1.mkdir()
    (p1 / "pyproject.toml").write_text("[project]\nname='p1'")

    discovery = ProjectDiscoveryService(authorized_roots=[root])
    return RPCDispatcher(discovery_service=discovery)


@pytest.mark.asyncio
async def test_rpc_ping(rpc_dispatcher: RPCDispatcher):
    req = RPCRequest(method="system.ping", params={"echo": "test-123"}, id="req-1")
    resp = await rpc_dispatcher.dispatch(req)
    assert resp.id == "req-1"
    assert resp.error is None
    assert resp.result["pong"] is True
    assert resp.result["echo"] == "test-123"


@pytest.mark.asyncio
async def test_rpc_capabilities_list(rpc_dispatcher: RPCDispatcher):
    req = RPCRequest(method="capabilities.list", params={}, id="req-2")
    resp = await rpc_dispatcher.dispatch(req)
    assert resp.error is None
    assert "process.list" in resp.result["capabilities"]
    assert "filesystem.discover" in resp.result["capabilities"]


@pytest.mark.asyncio
async def test_rpc_filesystem_discover(rpc_dispatcher: RPCDispatcher):
    req = RPCRequest(method="filesystem.discover", params={}, id="req-3")
    resp = await rpc_dispatcher.dispatch(req)
    assert resp.error is None
    assert resp.result["total"] == 1
    assert resp.result["projects"][0]["name"] == "my_project"


@pytest.mark.asyncio
async def test_rpc_filesystem_stat(rpc_dispatcher: RPCDispatcher, tmp_path: Path):
    safe_file = tmp_path / "authorized" / "my_project" / "pyproject.toml"
    req = RPCRequest(method="filesystem.stat", params={"path": str(safe_file)}, id="req-4")
    resp = await rpc_dispatcher.dispatch(req)
    assert resp.error is None
    assert resp.result["exists"] is True
    assert resp.result["is_file"] is True


@pytest.mark.asyncio
async def test_rpc_unknown_method_returns_32601(rpc_dispatcher: RPCDispatcher):
    req = RPCRequest(method="unsupported.shell_exec", params={"cmd": "ls"}, id="req-5")
    resp = await rpc_dispatcher.dispatch(req)
    assert resp.error is not None
    assert resp.error.code == RPCErrorCode.METHOD_NOT_FOUND


@pytest.mark.asyncio
async def test_rpc_path_traversal_returns_32002(rpc_dispatcher: RPCDispatcher):
    req = RPCRequest(method="filesystem.stat", params={"path": "/etc/passwd"}, id="req-6")
    resp = await rpc_dispatcher.dispatch(req)
    assert resp.error is not None
    assert resp.error.code == RPCErrorCode.FORBIDDEN
    assert "outside authorized roots" in resp.error.message


@pytest.mark.asyncio
async def test_rpc_invalid_params_returns_32602(rpc_dispatcher: RPCDispatcher):
    # max_depth must be <= 8, send 99
    req = RPCRequest(method="filesystem.discover", params={"max_depth": 99}, id="req-7")
    resp = await rpc_dispatcher.dispatch(req)
    assert resp.error is not None
    assert resp.error.code == RPCErrorCode.INVALID_PARAMS
