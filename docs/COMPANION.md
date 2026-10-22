# Personal OS — Local Companion Operations Guide (M2)

This guide documents the setup, configuration, operation, security model, and recovery procedures for the **Personal OS Local Companion daemon** (`pos-companion`).

---

## 1. Overview & Architecture

The Local Companion is a lightweight, user-space Python process that connects your local development machine to the Personal OS Cloud Control Plane.

### Key Invariants:
- **Outbound-only connection**: Initiates a persistent outbound WebSocket (`/ws/companion`). Never opens an inbound network port on your machine.
- **Narrowly bounded capabilities**: Executes only explicitly registered, declared capabilities (`system.ping`, `system.health`, `capabilities.list`, `process.list`, `filesystem.discover`, `filesystem.stat`). Arbitrary shell execution and command-line execution are strictly prohibited.
- **Strict identity separation**: Device identity (`dev_<hex>`) is distinct from user identity and authenticated using HMAC-SHA256 device tokens with scoped permissions.
- **Non-blocking offline resiliency**: The cloud control plane and task management features remain fully functional when the laptop companion is offline.
- **Safe error handling**: Disconnections, timeouts, and unexecuted requests are never reported as successful.

---

## 2. Installation & Prerequisites

The companion runs as a non-privileged user process using Python 3.11+ and `uv`.

```bash
# Clone the repository
git clone https://github.com/your-org/personal-os.git
cd personal-os

# Create and sync virtual environment
uv sync
source .venv/bin/activate
```

The CLI executable `pos-companion` is now available in your PATH within the virtual environment.

---

## 3. Configuration & Secrets Storage

Configuration is managed via environment variables (prefixed with `PERSONAL_OS_COMPANION_`) or a local `.env` file outside source control.

| Variable | Default | Purpose |
|----------|---------|---------|
| `PERSONAL_OS_COMPANION_DIR` | `~/.config/personal-os/companion` | Secure directory for device identity & credentials |
| `PERSONAL_OS_COMPANION_CLOUD_URL` | `http://localhost:8000` | Cloud Control Plane REST API endpoint |
| `PERSONAL_OS_COMPANION_WS_URL` | `ws://localhost:8000/ws/companion` | Session Broker WebSocket URL |
| `PERSONAL_OS_COMPANION_DEVICE_NAME` | Machine hostname (`socket.gethostname()`) | Display name of the companion device |
| `PERSONAL_OS_COMPANION_AUTHORIZED_ROOTS` | `~/Projects` or CWD | Approved filesystem boundaries for project discovery |
| `PERSONAL_OS_COMPANION_HEARTBEAT_INTERVAL_SECONDS` | `15.0` | Heartbeat cadence |
| `PERSONAL_OS_COMPANION_LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

### Secure Credential Storage Invariant
- The credentials file is saved to `~/.config/personal-os/companion/credentials.json`.
- The directory is created with `0700` (`rwx------`) permissions.
- The credentials file is stored with `0600` (`rw-------`) permissions.
- Stored tokens are never logged or exposed in diagnostic outputs.

---

## 4. First-Run Pairing & Registration

To pair a new companion device with your account:

### Step 1: Obtain a User Token
Log in to the Personal OS web portal (`http://localhost:5173` or your hosted domain). Retrieve your Bearer token or pass your access token.

### Step 2: Register Device
```bash
pos-companion register \
  --cloud-url http://localhost:8000 \
  --user-token "YOUR_USER_BEARER_TOKEN" \
  --name "ThinkPad Workstation"
```

Output:
```
Registering device 'ThinkPad Workstation' with http://localhost:8000...
Registration successful! Device paired.
Credentials saved to ~/.config/personal-os/companion/credentials.json (mode 0600)
```

---

## 5. Running the Companion

### Foreground / Development Mode
To start the daemon in the foreground with live diagnostic logs:
```bash
pos-companion run
```

Or with custom parameters:
```bash
pos-companion run --log-level DEBUG --cloud-url http://localhost:8000
```

### Clean Shutdown
The companion intercepts `SIGINT` (Ctrl+C) and `SIGTERM`. It cleanly closes the active WebSocket session, notifies the cloud server of offline status, and flushes resources within 5 seconds.

### Optional User-Level Background Service (Linux systemd user service)
Do NOT run the companion as root. You can configure a user-space systemd unit:

Create `~/.config/systemd/user/personal-os-companion.service`:
```ini
[Unit]
Description=Personal OS Local Companion Daemon
After=network.target

[Service]
Type=simple
ExecStart=%h/Projects/personal-os/.venv/bin/pos-companion run
Restart=always
RestartSec=5
Environment="PERSONAL_OS_COMPANION_LOG_LEVEL=INFO"

[Install]
WantedBy=default.target
```

Enable and start:
```bash
systemctl --user daemon-reload
systemctl --user enable --now personal-os-companion
systemctl --user status personal-os-companion
```

---

## 6. Status & Diagnostics

Check companion configuration, local identity, and pairing status without starting the daemon:

```bash
pos-companion status
```

Example output:
```
Device ID:        dev_37f82b1c49e29a01
Device Name:      arch-desktop
Platform:         linux
Cloud URL:        http://localhost:8000
WebSocket URL:    ws://localhost:8000/ws/companion
Config Directory: /home/user/.config/personal-os/companion
Credentials File: /home/user/.config/personal-os/companion/credentials.json (present)
Authorized Roots: /home/user/Projects
Status:           Paired and registered with cloud
```

---

## 7. Revocation & Unpairing

If a laptop is retired, lost, or re-installed:

### Server-side Revocation (Web UI or REST API):
In the Personal OS web portal:
1. Navigate to the **Devices** page.
2. Select the device and click **Revoke**.
3. All active WebSocket connections are terminated immediately with HTTP 4401 / JSON-RPC `-32001 UNAUTHORIZED`. Subsequent reconnect attempts are denied.

Or via API:
```bash
curl -X POST http://localhost:8000/api/v1/devices/dev_37f82b1c49e29a01/revoke \
  -H "Authorization: Bearer $USER_TOKEN"
```

### Client-side Credential Clearing:
To erase local credentials and device tokens:
```bash
pos-companion revoke
```
Output:
```
Credentials cleared for device at /home/user/.config/personal-os/companion/credentials.json
```

---

## 8. Capability Reference & Security Model

The companion dispatches incoming JSON-RPC 2.0 calls strictly according to the allowlist:

| Capability | Parameters | Execution Boundary | Security Invariant |
|------------|------------|--------------------|--------------------|
| `system.ping` | `{"echo": "..."}` | Echo payload & timestamp | In-memory only; max 5s timeout |
| `capabilities.list` | `{}` | Returns manifest | In-memory only |
| `process.list` | `{"limit": 50, "name_filter": "py"}` | Filtered process list | **Only user-owned processes** (`os.getuid()`). Never shows root/system processes. Command line parameters are sanitized. |
| `filesystem.discover` | `{"root": "/home/user/Projects", "max_depth": 4}` | Discovers git/python/node/rust projects | **Strictly confined to `authorized_roots`**. Symlinks outside authorized boundaries are rejected. Path traversal (`../`) is blocked. |
| `filesystem.stat` | `{"path": "/home/user/Projects/foo/README.md"}` | File/directory metadata | Never returns file contents; traversal outside authorized roots blocked. |

---

## 9. Failure Recovery & Resilience

1. **Network Disruption**: When the internet drops, the daemon enters a reconnect loop with bounded exponential backoff (1s minimum, 30s maximum) with random jitter to prevent reconnect storms.
2. **Server Restart**: The session broker restarts cleanly; active companions detect socket closure and reconnect automatically without user intervention.
3. **In-Flight Disconnect**: If the companion disconnects while executing an operation, the server marks the operation as `failed` (e.g. `"Device disconnected mid-operation"`). The cloud never treats an incomplete operation as completed.
4. **Stale Presence**: If no heartbeat is received for >60s, the server marks the device as `stale`. Once a heartbeat or reconnect arrives, status returns to `online`.
