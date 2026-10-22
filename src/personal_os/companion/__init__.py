"""Personal OS Local Companion daemon and client runtime (M2)."""

from personal_os.companion.protocol import (
    DEFAULT_CAPABILITIES,
    PROTOCOL_VERSION,
    PresenceState,
    RPCErrorCode,
    RPCException,
    RPCRequest,
    RPCResponse,
)

__all__ = [
    "PROTOCOL_VERSION",
    "PresenceState",
    "RPCErrorCode",
    "RPCException",
    "RPCRequest",
    "RPCResponse",
    "DEFAULT_CAPABILITIES",
]
