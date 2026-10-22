"""Versioned WebSocket JSON-RPC 2.0 protocol contract and schemas (M2-B).

Protocol version: personal-os-rpc/v1
Follows docs/PROTOCOL_CONTRACT.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum, StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

PROTOCOL_VERSION = "personal-os-rpc/v1"
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 15
DEFAULT_STALE_TIMEOUT_SECONDS = 60


# --------------------------------------------------------------------------
# JSON-RPC 2.0 Error Codes
# --------------------------------------------------------------------------


class RPCErrorCode(int, Enum):
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    OUTCOME_UNKNOWN = -32000
    UNAUTHORIZED = -32001
    FORBIDDEN = -32002
    TIMEOUT = -32003
    VERSION_MISMATCH = -32004


class RPCErrorData(BaseModel):
    code: int
    message: str
    data: dict[str, Any] | None = None


class RPCException(Exception):  # noqa: N818
    """Exception class that serializes directly to JSON-RPC error."""

    def __init__(
        self,
        code: int | RPCErrorCode,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = int(code)
        self.message = message
        self.data = data

    def to_rpc_error(self) -> RPCErrorData:
        return RPCErrorData(code=self.code, message=self.message, data=self.data)


# --------------------------------------------------------------------------
# Core JSON-RPC 2.0 Message Envelopes
# --------------------------------------------------------------------------


class RPCRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = Field(default_factory=lambda: str(uuid4()))
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class RPCResponse(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    result: Any | None = None
    error: RPCErrorData | None = None

    @classmethod
    def success(cls, req_id: str | int | None, result: Any) -> RPCResponse:
        return cls(id=req_id, result=result, error=None)

    @classmethod
    def failure(
        cls,
        req_id: str | int | None,
        code: int | RPCErrorCode,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> RPCResponse:
        return cls(
            id=req_id,
            result=None,
            error=RPCErrorData(code=int(code), message=message, data=data),
        )


# --------------------------------------------------------------------------
# Presence & Lifecycle States
# --------------------------------------------------------------------------


class PresenceState(StrEnum):
    CONNECTING = "connecting"
    ONLINE = "online"
    DEGRADED = "degraded"
    STALE = "stale"
    OFFLINE = "offline"


# --------------------------------------------------------------------------
# Handshake & Registration
# --------------------------------------------------------------------------


class HandshakeParams(BaseModel):
    protocol_version: str = PROTOCOL_VERSION
    device_id: str = Field(..., min_length=4, max_length=128)
    token: str = Field(..., min_length=1)
    client_version: str = "0.1.0"
    platform: str = "linux"
    supported_capabilities: list[str] = Field(default_factory=list)


class HandshakeResult(BaseModel):
    authenticated: bool
    session_id: str
    device_id: str
    user_id: str
    protocol_version: str = PROTOCOL_VERSION
    granted_capabilities: list[str]
    heartbeat_interval_seconds: int = DEFAULT_HEARTBEAT_INTERVAL_SECONDS
    stale_timeout_seconds: int = DEFAULT_STALE_TIMEOUT_SECONDS


# --------------------------------------------------------------------------
# Heartbeat
# --------------------------------------------------------------------------


class HeartbeatMetrics(BaseModel):
    cpu_percent: float | None = None
    memory_percent: float | None = None


class HeartbeatParams(BaseModel):
    device_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: PresenceState = PresenceState.ONLINE
    metrics: HeartbeatMetrics | None = None


class HeartbeatResult(BaseModel):
    acknowledged: bool = True
    server_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: PresenceState = PresenceState.ONLINE


# --------------------------------------------------------------------------
# Capability Manifest
# --------------------------------------------------------------------------


class CapabilityDetail(BaseModel):
    version: str = "1.0"
    description: str
    requires_approval: bool = False
    idempotent: bool = True


class CapabilityManifest(BaseModel):
    protocol_version: str = PROTOCOL_VERSION
    capabilities: dict[str, CapabilityDetail] = Field(default_factory=dict)


# Standard approved M2/M4 capabilities with explicit idempotency classification
DEFAULT_CAPABILITIES: dict[str, CapabilityDetail] = {
    "system.ping": CapabilityDetail(
        version="1.0",
        description="Liveness and round-trip ping",
        requires_approval=False,
        idempotent=True,
    ),
    "system.health": CapabilityDetail(
        version="1.0",
        description="Liveness and health diagnostics",
        requires_approval=False,
        idempotent=True,
    ),
    "capabilities.list": CapabilityDetail(
        version="1.0",
        description="List supported capabilities and versions",
        requires_approval=False,
        idempotent=True,
    ),
    "process.list": CapabilityDetail(
        version="1.0",
        description="Inspect processes owned by current user",
        requires_approval=False,
        idempotent=True,
    ),
    "filesystem.discover": CapabilityDetail(
        version="1.0",
        description="Discover local projects under authorized roots",
        requires_approval=False,
        idempotent=True,
    ),
    "filesystem.stat": CapabilityDetail(
        version="1.0",
        description="Inspect file and directory metadata within authorized roots",
        requires_approval=False,
        idempotent=True,
    ),
}

IDEMPOTENT_CAPABILITIES: dict[str, bool] = {
    k: v.idempotent for k, v in DEFAULT_CAPABILITIES.items()
}


# --------------------------------------------------------------------------
# Process Inspection
# --------------------------------------------------------------------------


class ProcessInfo(BaseModel):
    pid: int
    name: str
    cpu_percent: float | None = None
    memory_percent: float | None = None
    memory_rss_bytes: int | None = None
    status: str = "running"
    username: str | None = None
    create_time: float | None = None


class ProcessListParams(BaseModel):
    limit: int = Field(default=100, ge=1, le=500)
    name_filter: str | None = None


class ProcessListResult(BaseModel):
    processes: list[ProcessInfo]
    total: int


# --------------------------------------------------------------------------
# Filesystem Discovery & Stat
# --------------------------------------------------------------------------


class DiscoveredProject(BaseModel):
    name: str
    path: str
    project_type: str
    has_git: bool = False
    indicators: list[str] = Field(default_factory=list)
    last_modified: datetime | None = None


class DiscoveryParams(BaseModel):
    root: str | None = None
    max_depth: int = Field(default=4, ge=1, le=8)


class DiscoveryResult(BaseModel):
    projects: list[DiscoveredProject]
    scanned_roots: list[str]
    total: int


class FileStatParams(BaseModel):
    path: str


class FileStatResult(BaseModel):
    path: str
    exists: bool
    is_dir: bool = False
    is_file: bool = False
    is_symlink: bool = False
    size_bytes: int = 0
    modified_at: datetime | None = None
