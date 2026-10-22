# Personal OS — Companion WebSocket Protocol Contract (v1)

**Status**: Frozen for Milestone 2 implementation.
**Protocol Version**: `personal-os-rpc/v1`
**Base Specification**: JSON-RPC 2.0 over WebSocket

---

## 1. Overview

The companion initiates an outbound WebSocket connection to the Personal OS Session Broker / Cloud Control Plane (`/ws/companion`).
The laptop never exposes an inbound network port.

The protocol provides:
1. Authenticated connection handshake and capability negotiation.
2. Bidirectional JSON-RPC 2.0 communication.
3. Periodic heartbeat reporting and presence tracking (`connecting`, `online`, `degraded`, `offline`, `stale`).
4. Reconnection with bounded exponential backoff and jitter.
5. Scoped, safe operations: process listing (user-owned only), filesystem project discovery (within authorized roots only), and health/diagnostic checks.

---

## 2. Framing and Envelopes (JSON-RPC 2.0)

All WebSocket text messages must be UTF-8 JSON objects conforming to the JSON-RPC 2.0 specification.

### 2.1 Request Envelope
```json
{
  "jsonrpc": "2.0",
  "id": "req-01JABC123",
  "method": "process.list",
  "params": {}
}
```

- `jsonrpc`: Must be exactly `"2.0"`.
- `id`: Unique string or integer correlation identifier. Omitted for notifications.
- `method`: Namespaced method identifier string (`<domain>.<operation>`).
- `params`: JSON object containing method-specific parameters.

### 2.2 Response Envelope (Success)
```json
{
  "jsonrpc": "2.0",
  "id": "req-01JABC123",
  "result": {
    "processes": [
      {
        "pid": 1234,
        "name": "python",
        "cpu_percent": 1.2,
        "memory_rss_bytes": 45120000,
        "username": "user",
        "status": "running"
      }
    ]
  }
}
```

### 2.3 Response Envelope (Error)
```json
{
  "jsonrpc": "2.0",
  "id": "req-01JABC123",
  "error": {
    "code": -32002,
    "message": "Path outside authorized roots",
    "data": {
      "path": "/etc/passwd",
      "authorized_roots": ["/home/user/Projects"]
    }
  }
}
```

### 2.4 Standard Error Codes
| Code | Constant | Meaning |
|------|----------|---------|
| `-32700` | `PARSE_ERROR` | Invalid JSON received |
| `-32600` | `INVALID_REQUEST` | Not a valid JSON-RPC 2.0 request |
| `-32601` | `METHOD_NOT_FOUND` | Method does not exist or capability not granted |
| `-32602` | `INVALID_PARAMS` | Method parameters invalid or validation failed |
| `-32603` | `INTERNAL_ERROR` | Internal server or companion execution error |
| `-32001` | `UNAUTHORIZED` | Missing, invalid, or expired credentials |
| `-32002` | `FORBIDDEN` | Access denied (e.g., path traversal, cross-user, revoked device) |
| `-32003` | `TIMEOUT` | Request timed out |
| `-32004` | `VERSION_MISMATCH` | Incompatible protocol version |

---

## 3. Handshake & Capability Negotiation

Upon establishing the WebSocket connection, the companion must send an `auth.handshake` request as its first message.
No other RPC methods are processed until the handshake succeeds.

### 3.1 Handshake Request (`auth.handshake`)
```json
{
  "jsonrpc": "2.0",
  "id": "hs-1",
  "method": "auth.handshake",
  "params": {
    "protocol_version": "personal-os-rpc/v1",
    "device_id": "dev_01JABC123XYZ",
    "token": "<device_auth_token>",
    "client_version": "0.1.0",
    "platform": "linux",
    "supported_capabilities": [
      "system.health",
      "process.list",
      "filesystem.discover",
      "filesystem.stat"
    ]
  }
}
```

### 3.2 Handshake Success Response
```json
{
  "jsonrpc": "2.0",
  "id": "hs-1",
  "result": {
    "authenticated": true,
    "session_id": "sess_01JDEF456",
    "device_id": "dev_01JABC123XYZ",
    "user_id": "usr_01JGHI789",
    "protocol_version": "personal-os-rpc/v1",
    "granted_capabilities": [
      "system.health",
      "process.list",
      "filesystem.discover",
      "filesystem.stat"
    ],
    "heartbeat_interval_seconds": 15,
    "stale_timeout_seconds": 60
  }
}
```

### 3.3 Handshake Failure
If the token is invalid, expired, or the device is revoked:
```json
{
  "jsonrpc": "2.0",
  "id": "hs-1",
  "error": {
    "code": -32001,
    "message": "Device authentication failed: revoked or invalid token",
    "data": { "revoked": true }
  }
}
```
The connection is terminated by the server immediately following an authentication error.

If the protocol version is unsupported:
```json
{
  "jsonrpc": "2.0",
  "id": "hs-1",
  "error": {
    "code": -32004,
    "message": "Unsupported protocol version",
    "data": { "supported_versions": ["personal-os-rpc/v1"] }
  }
}
```

---

## 4. Heartbeats & Presence States

### 4.1 Presence States
1. **`connecting`**: Socket connected, awaiting successful `auth.handshake`.
2. **`online`**: Authenticated and reporting periodic heartbeats.
3. **`degraded`**: Authenticated, but experiencing resource constraints, missed heartbeat, or partial failures.
4. **`stale`**: No heartbeat received for > `stale_timeout_seconds` (default: 60s).
5. **`offline`**: Disconnected, clean socket closure, or expired presence.

### 4.2 Heartbeat Notification / Request (`device.heartbeat`)
Cadence: Every 15 seconds (default).
```json
{
  "jsonrpc": "2.0",
  "id": "hb-102",
  "method": "device.heartbeat",
  "params": {
    "device_id": "dev_01JABC123XYZ",
    "timestamp": "2026-10-18T18:00:00Z",
    "status": "online",
    "metrics": {
      "cpu_percent": 3.4,
      "memory_percent": 42.1
    }
  }
}
```
Server response:
```json
{
  "jsonrpc": "2.0",
  "id": "hb-102",
  "result": {
    "acknowledged": true,
    "server_timestamp": "2026-10-18T18:00:00.120Z",
    "status": "online"
  }
}
```

---

## 5. M2 RPC Capability Manifest

### 5.1 `system.ping`
Checks connection liveness and round-trip time.
- **Params**: `{"echo": "optional_string"}`
- **Result**: `{"pong": true, "echo": "optional_string", "timestamp": "2026-10-18T18:00:00Z"}`

### 5.2 `capabilities.list`
Returns the capability manifest supported by the device.
- **Params**: `{}`
- **Result**:
  ```json
  {
    "protocol_version": "personal-os-rpc/v1",
    "capabilities": {
      "system.health": { "version": "1.0", "description": "Health and ping" },
      "process.list": { "version": "1.0", "description": "User-scoped process inspection" },
      "filesystem.discover": { "version": "1.0", "description": "Project discovery within authorized roots" },
      "filesystem.stat": { "version": "1.0", "description": "Narrow metadata inspection within authorized roots" }
    }
  }
  ```

### 5.3 `process.list`
Inspects processes owned strictly by the current user (`os.getuid()`).
- **Security Invariant**: Never exposes root processes or processes belonging to other users. Command lines are sanitized to avoid credential leakage.
- **Params**:
  - `limit`: Optional integer (default: 100, max: 500)
  - `name_filter`: Optional substring match
- **Result**:
  ```json
  {
    "processes": [
      {
        "pid": 4821,
        "name": "python3",
        "cpu_percent": 0.5,
        "memory_percent": 1.2,
        "memory_rss_bytes": 35651584,
        "status": "sleeping",
        "create_time": 1729270000.0
      }
    ],
    "total": 1
  }
  ```

### 5.4 `filesystem.discover`
Discovers projects located strictly under explicitly configured authorized roots.
- **Security Invariant**:
  - All scanned paths must resolve strictly within an authorized root.
  - Symlinks pointing outside authorized roots are detected and rejected/ignored.
  - Traversal attempts (`../`, `~`, `/etc`) return `-32002 FORBIDDEN`.
- **Params**:
  - `root`: Optional path string to restrict search to a specific authorized root (must match or be subpath of an authorized root).
  - `max_depth`: Optional integer (default: 4, max: 8).
- **Result**:
  ```json
  {
    "projects": [
      {
        "name": "personal-os",
        "path": "/home/user/Projects/personal-os",
        "project_type": "python",
        "has_git": true,
        "indicators": [".git", "pyproject.toml"],
        "last_modified": "2026-10-18T17:50:00Z"
      }
    ],
    "scanned_roots": ["/home/user/Projects"],
    "total": 1
  }
  ```

### 5.5 `filesystem.stat`
Inspects file or directory metadata strictly within authorized roots.
- **Security Invariant**: Traversal outside authorized roots is rejected with `-32002 FORBIDDEN`. Symlinks resolving outside are rejected. File content is never returned.
- **Params**:
  - `path`: Path string relative to an authorized root or absolute path within an authorized root.
- **Result**:
  ```json
  {
    "path": "/home/user/Projects/personal-os/pyproject.toml",
    "exists": true,
    "is_dir": false,
    "is_file": true,
    "is_symlink": false,
    "size_bytes": 1491,
    "modified_at": "2026-10-18T17:50:00Z"
  }
  ```

---

## 6. Local Identity & Credentials Storage Contract

1. **Storage Path**: `~/.config/personal-os/companion/` (or overridden by environment `PERSONAL_OS_COMPANION_DIR`).
2. **File Permissions**:
   - Directory: `0700` (`rwx------`)
   - Credentials file: `credentials.json` with `0600` (`rw-------`)
3. **Data Schema (`credentials.json`)**:
   ```json
   {
     "device_id": "dev_01JABC123XYZ",
     "device_name": "My Laptop",
     "registered_at": "2026-10-18T17:00:00Z",
     "token": "eyJhbGciOi...",
     "user_id": "usr_01JGHI789",
     "cloud_url": "https://api.pos-deku.com",
     "ws_url": "wss://api.pos-deku.com/ws/companion"
   }
   ```
4. **Safety Invariants**:
   - Credentials are never logged, checked into version control, or returned over public diagnostic APIs.
   - Credentials survive ordinary companion process restarts.
