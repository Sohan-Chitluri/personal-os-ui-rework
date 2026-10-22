# ADR 0002 — M2 Local Companion & Capability Execution Contract

- **Status**: Accepted
- **Date**: 2026-10-19
- **Deciders**: Personal OS Core Architecture
- **Invariants Reference**: `docs/PROTOCOL_CONTRACT.md`, `docs/ARCHITECTURE.md`, `docs/DATA_MODEL.md`

---

## 1. Context and Problem Statement

Personal OS requires interaction between the cloud application (FastAPI control plane, PostgreSQL, background tasks) and approved capabilities on a user's local machine (e.g., laptop). The user may be away from the machine and issue requests via the cloud web app or mobile capture.

The companion must:
1. **Never expose inbound network ports** on the user's laptop.
2. **Never expose unrestricted command execution**, arbitrary shell access, or raw filesystem escapes.
3. Maintain clear **separation of device identity from user identity**.
4. Guarantee that **laptop offline state never disrupts cloud task/project workflows**.
5. Ensure the cloud **never treats unexecuted or partially executed local actions as successful**.

---

## 2. Decision Outcomes

### 2.1 Device Identity & Lifecycle

- **Stable Device Identifier**: Formatted as `dev_<hex16>` (e.g., `dev_01jabc123xyz`). Created locally or assigned by server; persisted in `credentials.json` with permissions `0600` in directory `0700`.
- **Owning User**: Stored as `owner_id` (foreign key to `users.id`). All device operations, queries, and revocations are strictly scoped to `owner_id`.
- **Platform Metadata**: Device reports `name` (hostname), `platform` (`linux`, `darwin`, `win32`), and client version.
- **Credential Issuance & Scoping**:
  - The server issues a scoped JWT with claims `{"sub": device_id, "device_id": device_id, "user_id": user_id, "token_use": "device", "capabilities": {...}, "exp": ...}`.
  - Device IDs are never accepted as credentials; proof of possession of a valid signed JWT is mandatory.
- **Revocation**:
  - Revocation is immediate via `POST /api/v1/devices/{device_id}/revoke`.
  - Sets `revoked_at = now()`, transitions status to `offline`.
  - Active WebSocket connections are severed immediately with code `4401` and error `UNAUTHORIZED (-32001)`.
  - The companion clears its stored credentials upon encountering a revocation response.

### 2.2 Capability Declaration & Registry

Capabilities are versioned, declared in an allowlist, and negotiated during the handshake:

| Capability | Version | Description | Approval Req. | Timeout | Resource Limits |
|------------|---------|-------------|---------------|---------|-----------------|
| `system.ping` | 1.0 | Round-trip latency & diagnostic echo | No | 5s | Bounded JSON |
| `system.health` | 1.0 | CPU & memory utilization metrics | No | 5s | Single /proc read |
| `capabilities.list` | 1.0 | Return supported capability manifest | No | 5s | Static in-memory |
| `process.list` | 1.0 | List user-owned processes (`os.getuid()`) | No | 10s | Max 500 processes, command line sanitized |
| `filesystem.discover` | 1.0 | Discover projects in authorized roots | No | 30s | Max depth 8, symlinks outside roots ignored |
| `filesystem.stat` | 1.0 | File/directory metadata within authorized roots | No | 10s | Metadata only, no file content |

**Execution Safety Invariants**:
- Arbitrary shell execution (`/bin/sh`, `exec`, `eval`) is strictly prohibited.
- Filesystem access is strictly bounded to `authorized_roots` (default: `~/Projects`).
- Path traversal (`../`, absolute paths outside roots) returns `FORBIDDEN (-32002)`.

### 2.3 Request and Result Lifecycle

Device operations initiated by the user/cloud follow an explicit state machine:

```
[User Request]
       │
       ▼
  (pending) ───[Device Offline]───► (failed)
       │
  [Dispatched to WS]
       │
       ▼
  (running) ───[Timeout / Disconnect]───► (failed / timeout)
       │
  [Companion Reply]
       │
       ▼
 (completed)
```

- **Unique Request ID**: ULID string assigned to every `DeviceOperation` record.
- **Idempotency**: Client supplies optional `idempotency_key`. The combination `(owner_id, device_id, idempotency_key)` is enforced unique at the database level. Replaying an idempotency key returns the existing operation without re-executing.
- **Deadline and Timeout**: Cloud enforces a configurable timeout (default: 15s). If no response is received before timeout, the operation transitions to `timeout` and is recorded as failed.
- **Disconnect Behavior**: If the companion disconnects while an operation is in the `running` state, the connection manager marks all active futures as aborted and the operation transitions to `failed` with error message `"Device disconnected mid-operation"`. The cloud never assumes success.
- **Audit Trail**: Every request, input parameters, timestamp, execution duration, and structured output or error is persisted in `device_operations`.

---

## 3. Threat Model & Mitigations

1. **Stolen Device ID**: Device ID is an identifier, not a credential. Handshake requires a valid HMAC-SHA256 JWT signed by the cloud secret.
2. **Unauthorized Pairing**: Device registration requires an authenticated user bearer token. Cross-user registration of an existing `device_id` returns HTTP 409 Conflict.
3. **Revoked Device Reconnect**: The server verifies `revoked_at is NULL` on handshake and periodic heartbeat. Revocation immediately terminates open sockets.
4. **Replayed Requests**: Requests carry unique IDs and optional idempotency keys. Duplicate deliveries are ignored.
5. **Path Traversal / Symlink Attacks**: Companion normalizes and resolves all paths with `.resolve()` and checks `.is_relative_to(root)` against configured authorized roots. Symlinks pointing outside are rejected.
6. **Information Leakage**: Process listing filters strictly by `uid == os.getuid()`. Sensitive tokens/passwords in process command lines are stripped.
