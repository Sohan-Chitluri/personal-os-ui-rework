"""Companion local JSON-RPC 2.0 request dispatcher (M2-A).

Dispatches approved capability methods:
- system.ping
- capabilities.list
- process.list
- filesystem.discover
- filesystem.stat
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from personal_os.companion.discovery import ProjectDiscoveryService
from personal_os.companion.process import ProcessInspector
from personal_os.companion.protocol import (
    DEFAULT_CAPABILITIES,
    PROTOCOL_VERSION,
    CapabilityDetail,
    DiscoveryParams,
    FileStatParams,
    ProcessListParams,
    RPCErrorCode,
    RPCException,
    RPCRequest,
    RPCResponse,
)

log = logging.getLogger("personal_os.companion.rpc")


class RPCDispatcher:
    """Dispatches incoming JSON-RPC 2.0 requests to local capabilities."""

    def __init__(
        self,
        discovery_service: ProjectDiscoveryService,
        process_inspector: ProcessInspector | None = None,
        capabilities: dict[str, CapabilityDetail] | None = None,
    ) -> None:
        self.discovery = discovery_service
        self.process = process_inspector or ProcessInspector()
        self.capabilities = capabilities or DEFAULT_CAPABILITIES

    async def dispatch(self, request: RPCRequest) -> RPCResponse:
        """Execute request and return formatted JSON-RPC 2.0 response."""
        method = request.method
        req_id = request.id
        params = request.params or {}

        try:
            handler = getattr(self, f"rpc_{method.replace('.', '_')}", None)
            if not handler:
                return RPCResponse.failure(
                    req_id,
                    RPCErrorCode.METHOD_NOT_FOUND,
                    f"Method '{method}' not found or unsupported",
                )

            result = await handler(params)
            return RPCResponse.success(req_id, result)
        except RPCException as exc:
            return RPCResponse.failure(req_id, exc.code, exc.message, exc.data)
        except Exception as exc:
            log.exception("Unexpected error executing RPC method %s: %s", method, exc)
            return RPCResponse.failure(
                req_id,
                RPCErrorCode.INTERNAL_ERROR,
                f"Internal execution error: {exc}",
            )

    async def rpc_system_ping(self, params: dict[str, Any]) -> dict[str, Any]:
        """Respond to ping."""
        return {
            "pong": True,
            "echo": params.get("echo"),
            "timestamp": datetime.now(UTC).isoformat(),
        }

    async def rpc_capabilities_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return capability manifest."""
        return {
            "protocol_version": PROTOCOL_VERSION,
            "capabilities": {k: v.model_dump() for k, v in self.capabilities.items()},
        }

    async def rpc_process_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Inspect user processes."""
        try:
            parsed = ProcessListParams.model_validate(params)
        except Exception as exc:
            raise RPCException(
                code=RPCErrorCode.INVALID_PARAMS,
                message=f"Invalid params for process.list: {exc}",
            ) from exc

        result = self.process.list_processes(parsed)
        return result.model_dump()

    async def rpc_filesystem_discover(self, params: dict[str, Any]) -> dict[str, Any]:
        """Discover projects under authorized roots."""
        try:
            parsed = DiscoveryParams.model_validate(params)
        except Exception as exc:
            raise RPCException(
                code=RPCErrorCode.INVALID_PARAMS,
                message=f"Invalid params for filesystem.discover: {exc}",
            ) from exc

        result = self.discovery.discover_projects(
            target_root=parsed.root,
            max_depth=parsed.max_depth,
        )
        return result.model_dump(mode="json")

    async def rpc_filesystem_stat(self, params: dict[str, Any]) -> dict[str, Any]:
        """Stat file or directory within authorized roots."""
        try:
            parsed = FileStatParams.model_validate(params)
        except Exception as exc:
            raise RPCException(
                code=RPCErrorCode.INVALID_PARAMS,
                message=f"Invalid params for filesystem.stat: {exc}",
            ) from exc

        result = self.discovery.stat_path(parsed.path)
        return result.model_dump(mode="json")
