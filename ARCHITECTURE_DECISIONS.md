# Architecture Decisions — Deku / Pos-Deku Backend

**Status**: Locked for initial delivery. Cross-referenced by all other architecture documents.

---

## 1. Deployment Architecture — Hybrid Local + AWS

| Layer | Technology | Responsibility |
|-------|------------|----------------|
| **Local Companion** | Python daemon (uv-managed) | Device registration, presence, authenticated outbound connection, local project discovery, approved remote actions |
| **Compute (AWS)** | EC2 (t3.medium or equivalent) | Persistent Python API + background worker + session broker for remote access |
| **Database** | RDS PostgreSQL 16+ | Canonical operational database |
| **File Storage** | S3 | Frontend assets, user uploads, generated artifacts, workspace templates |
| **CDN** | CloudFront | Frontend delivery, TLS termination via ACM |
| **Auth** | Cognito User Pools | Authentication, JWT issuance, MFA, device credentials |
| **Queue** | SQS (FIFO) | Async processing jobs (captures, exports, sync, workspace provisioning) |
| **Secrets** | Secrets Manager | DB credentials, OpenRouter API key, external secrets, companion tokens |
| **Observability** | CloudWatch | Logs, metrics, alarms, X-Ray traces |
| **DNS** | Existing registrar (or Route 53) | Domain → CloudFront distribution |
| **Remote Access** | SSM / WebSocket proxy on EC2 | Secure terminal broker for local companion + cloud workspaces |

**Explicitly excluded**: Lambda (except workspace provisioning triggers), API Gateway, EventBridge, DynamoDB, ElastiCache, ECS/EKS, Step Functions.

---

## 2. Three Execution Environments

### A. Local Machine — Primary Workspace
- Lightweight **local companion daemon** (`pos-companion`):
  - Registers device with AWS (Cognito device credentials + device registry table)
  - Maintains persistent authenticated **outbound WebSocket** to EC2 session broker
  - Reports online/offline status, local project discovery, system capabilities
  - Exposes **narrowly scoped RPC interface** for approved remote actions (terminal, file read, process list, agent orchestration)
  - Queues offline operations (captures, task edits) in local SQLite; reconciles on reconnect
  - No inbound ports, no unrestricted shell, no arbitrary command execution
  - Optional: local agent orchestration (Hermes, other tools) via approved RPC
- **Optional**: Local TUI/CLI preserved for offline-first workflow (reads from local cache + local vault)

### B. AWS — Persistent Control Plane
- **FastAPI on EC2**: Web portal API, authentication, authorization, operational records
- **RDS PostgreSQL**: Canonical source of truth for operational entities (tasks, projects, captures, focus sessions, sync metadata, device registry)
- **SQS FIFO**: Capture processing, export jobs, sync reconciliation, **workspace provisioning requests**
- **S3**: Frontend assets, user uploads, workspace templates (devcontainer/Nix flakes), generated artifacts
- **Session Broker** (on EC2): WebSocket hub for local companion ↔ portal terminal sessions; multiplexes approved RPC calls
- **CloudWatch + Secrets Manager**: Observability, credential management

### C. On-Demand Cloud Development Workspaces — Milestone 5
- **Provisioning**: SQS message → Lambda/EC2 worker → launches isolated EC2 instance from controlled template
- **Repository**: Clones selected GitHub repo (PAT or GitHub App token) into workspace
- **Environment**: Applies project-specific devcontainer / Nix flake / setup script (user-defined per project)
- **Tools**: Starts approved dev tools (VS Code Server, tmux, Hermes agent, language servers)
- **Access**: Secure WebSocket terminal via session broker; portal embeds xterm.js
- **Handoff**: Git commits/branches pushed to origin; no silent modification of laptop working copy
- **Lifecycle**: Configurable idle timeout, max runtime, cost limits, cleanup on termination
- **Secrets**: Injected at runtime via Secrets Manager / SSM Parameter Store; never baked into AMI

---

## 3. Backend Structure (Extended)

```
src/personal_os/
├── api/                    # FastAPI layer (thin HTTP handlers only)
│   ├── routes/
│   │   ├── tasks.py
│   │   ├── projects.py
│   │   ├── captures.py
│   │   ├── processing.py
│   │   ├── focus.py
│   │   ├── stats.py
│   │   ├── files.py
│   │   ├── search.py
│   │   ├── export.py
│   │   ├── devices.py           # NEW: Device registry, presence, capabilities
│   │   ├── companion.py         # NEW: Companion RPC endpoints (terminal, fs, process)
│   │   └── workspaces.py        # NEW: Cloud workspace provisioning, lifecycle
│   ├── schemas/
│   ├── deps.py
│   └── main.py
│
├── services/               # Domain service layer (business logic)
│   ├── tasks.py
│   ├── projects.py
│   ├── captures.py
│   ├── processing.py
│   ├── focus.py
│   ├── stats.py
│   ├── files.py
│   ├── search.py
│   ├── devices.py             # NEW: DeviceService
│   ├── companion.py           # NEW: CompanionService (RPC authorization, audit)
│   └── workspaces.py          # NEW: WorkspaceService (provision, lifecycle)
│
├── core/                   # Existing domain logic (PRESERVED, minimally adapted)
│   ├── capture.py
│   ├── processor.py
│   ├── extraction/
│   ├── llm.py / llm_openrouter.py
│   ├── config.py
│   └── vault.py              # REPLACED by SQLAlchemy repositories
│
├── db/                     # SQLAlchemy + Alembic
│   ├── models/             # + Device, WorkspaceTemplate, WorkspaceInstance, SyncMetadata
│   ├── repositories/
│   ├── migrations/
│   └── session.py
│
├── workers/                # Background worker entry point
│   ├── capture_worker.py
│   ├── workspace_worker.py    # NEW: Workspace provisioning handler
│   └── __main__.py
│
├── companion/              # NEW: Local companion daemon (separate package entry)
│   ├── daemon.py
│   ├── rpc/                # Approved RPC methods (terminal, fs, process, agent)
│   ├── discovery.py        # Local project discovery
│   └── auth.py             # Device credential management
│
└── cli/                    # EXISTING (preserved, not enhanced)
    └── main.py
```

**Key invariants** (unchanged):
- FastAPI routes contain **zero business logic**
- Services contain **all business logic**
- `core/extraction/` is the **single source of truth** for semantic extraction
- `core/processor.py` adapted: `BackgroundProcessor` → worker message handler; queue = SQS
- `core/vault.py` **replaced** by SQLAlchemy repositories

---

## 4. Data Ownership — Refined Source-of-Truth Boundaries

| Domain | Canonical Store | Sync Mechanism | Notes |
|--------|-----------------|----------------|-------|
| **Tasks, deadlines, focus sessions, activity records, captures, processing status** | PostgreSQL | Revision-checked synchronization with idempotency keys | Cloud-backed canonical; browser pending operations use IndexedDB |
| **Source code & project history** | Git (GitHub/GitLab) | Git push/pull/branch/merge | Each environment has own checkout; no FS replication |
| **Uncommitted source files** | Working copy where edited | Explicit handoff (Git commit/push, PR, workspace handoff) | Not auto-synced |
| **Markdown project notes, decisions, documents** | PostgreSQL (`project_documents`) + Markdown export | Bidirectional sync with conflict resolution (per-document) | Human-readable; export/import for portability |
| **Binary files & generated artifacts** | S3 + PostgreSQL metadata | Explicit upload/download; versioned in S3 | No auto-sync |
| **Device state & remote sessions** | PostgreSQL (`devices`, `sessions`) | Companion reports presence; TTL-based expiry | Device not "available" when disconnected |
| **Workspace templates** | S3 (devcontainer.json, Nix flakes, setup.sh) + PostgreSQL metadata | Versioned; per-project selection | Not in Git repo (user-defined) |

**No continuous bidirectional filesystem replication.** Git for source; scoped sync for operational records and Markdown.

---

## 5. Conflict Resolution & Synchronization — Extended Contract

### Default Policy: Last-Write-Wins with Safeguards

Every mutable operational record has:
- `id` (ULID, stable)
- `updated_at` (server-managed, timestamptz)
- `revision` (integer, monotonically increasing)
- `device_id` (originating device)

### Online Edits
- Optimistic locking via `If-Match: <revision>` header
- Match → apply, increment revision
- Mismatch → **409 Conflict** with current server state

### Offline Edits (All Milestones)
- Client queues: `operation_id` (ULID), `client_updated_at`, `device_id`, `base_revision`, `payload`
- On reconnect: client sends queued operations in `client_updated_at` order
- Server validates monotonicity per device
- For each operation:
  1. **Base revision matches** → apply, increment revision
  2. **Base revision differs** → LWW **only when**:
     - Server `updated_at` > client `client_updated_at` by **≥ 5 minutes** (configurable margin)
     - Operation type in **safe list**: task completion, title edit, deadline extension, focus session log
  3. **Otherwise** → store as `conflict_record` (server state + client payload) for user resolution
- Idempotency: `operation_id` deduplication (unique constraint on `sync_metadata`)

### Per-Data-Type Policies

| Data Type | Conflict Policy | Notes |
|-----------|-----------------|-------|
| Tasks (title, estimate, mood, frog) | LWW safe | Idempotent |
| Task completion (`done` toggle) | LWW safe | Idempotent |
| Task deadline (`due_in`) | LWW if server time clearly later | Else conflict |
| Task project move (`project_id`) | **Conflict** | Structural |
| Project hierarchy (`parent_id`) | **Conflict** | Structural |
| Project documents (Markdown) | **Per-document LWW with conflict preservation** | Both versions retained; user merges |
| Captures (raw text) | **Always append** | Immutable |
| Focus sessions | LWW safe | Append-only mostly |
| Device presence | Server TTL wins | No client authority |
| Workspace state | Server wins | Ephemeral |

### Deletion & Recovery
- Soft delete (`deleted_at`) for all operational records
- LWW on restore vs delete
- Conflict records retained for 90 days
- Never silently discard local work on reconnect

---

## 6. AWS Service Responsibilities (Extended)

| Service | Role | Configuration |
|---------|------|---------------|
| EC2 | API + Worker + Session Broker | ASG min=1, max=2; SSM for deploy; WebSocket hub on :8001 |
| RDS PostgreSQL | Primary DB | Multi-AZ prod; db.t3.micro dev / db.t3.small prod; encrypted; backups |
| S3 | Files + Frontend + Workspace Templates | Versioning; CORS for uploads; lifecycle for artifacts |
| CloudFront | Frontend delivery | OAI to S3; ACM; CSP headers |
| Cognito | Auth + Device Credentials | User pool + app client; **device tracking enabled**; JWT with `device_id` claim |
| SQS | Async queue | FIFO for captures; Standard for workspace provisioning; DLQs |
| Secrets Manager | Secrets | `database_url`, `openrouter_api_key`, `cognito_*`, `github_token` (for workspace), `companion_jwt_secret` |
| CloudWatch | Observability | Log groups; metric filters; alarms |
| IAM | Least privilege | EC2 profile: RDS, S3, SQS, Secrets, SSM, EC2 (for workspace launch) |
| SSM | Remote commands / Session Manager | Used by workspace worker for instance bootstrap |
| ACM | TLS | Wildcard cert; auto-renewal |

---

## 7. Remote Access Security Model

### Companion ↔ AWS Connection
- **Outbound-only** WebSocket from companion to EC2 session broker
- **Mutual TLS** or **JWT device credentials** (Cognito device tracking + custom claims)
- Session broker validates device identity, checks authorization, multiplexes RPC

### Approved RPC Methods (Capability-Based)
| Capability | Methods | Authorization |
|------------|---------|---------------|
| `terminal.read` | `pty_read`, `pty_list` | User + device approval |
| `terminal.write` | `pty_write`, `pty_resize` | User + device approval; explicit session start |
| `terminal.control` | `pty_spawn`, `pty_kill` | User + device approval; MFA for spawn |
| `filesystem.read` | `fs_read`, `fs_list`, `fs_stat` | Path allowlist (project dirs only) |
| `filesystem.write` | `fs_write`, `fs_mkdir` | Path allowlist; explicit confirmation |
| `process.list` | `ps_list` | User + device approval |
| `process.exec` | `ps_exec` | **Explicit per-command approval**; allowlist |
| `agent.orchestrate` | `agent_spawn`, `agent_status` | User + device approval; Hermes-specific |

### Session Lifecycle
1. Portal requests capability → AWS checks device online + user approval
2. Short-lived **session token** (5-15 min, renewable) issued to portal
3. Portal opens WebSocket to session broker with token
4. Broker forwards to companion; companion validates token + capability
5. All RPC calls logged (user, device, capability, command, timestamp)
6. Session ends on: explicit close, token expiry, device offline, revocation

### Revocation & Audit
- User can revoke device approval → immediate session termination
- Admin can revoke all sessions for user
- Audit log in `session_audit` table (immutable append-only)

---

## 8. Cloud Workspace Provisioning Design (Milestone 5)

### Request Flow
```
Portal → POST /api/v1/workspaces → WorkspaceService → SQS (workspace-provisioning)
  → WorkspaceWorker (Lambda or EC2) → EC2 RunInstances (from template)
    → UserData: clone repo, apply devcontainer/Nix, start tools
    → Register instance in `workspace_instances` table
    → Return connection info (WebSocket URL, instance ID)
```

### Template Strategy
- **Base AMI**: Amazon Linux 2023 + Docker + common tools
- **Per-project config** (stored in S3, selected at provision):
  - `devcontainer.json` (preferred) — VS Code Dev Container spec
  - `flake.nix` — Nix flake for reproducible environment
  - `setup.sh` — Fallback bash script
- Worker detects and applies appropriate method

### Instance Permissions (IAM Role)
```json
{
  "Effect": "Allow",
  "Action": [
    "ssm:GetParameters",
    "secretsmanager:GetSecretValue",
    "s3:GetObject",
    "s3:PutObject"
  ],
  "Resource": "*",
  "Condition": {
    "StringEquals": { "aws:ResourceTag/WorkspaceId": "${workspace_id}" }
  }
}
```

### Cost Controls
- Max concurrent workspaces per user: 2 (configurable)
- Max runtime: 4 hours (configurable)
- Idle timeout: 30 min (configurable)
- Instance type: t3.medium default; user can request larger (quota)
- CloudWatch metric → alarm → auto-terminate on limit breach

### Cleanup & Handoff
- On termination: Git status checked; uncommitted changes → pushed to `workspace/<id>/<timestamp>` branch
- User notified with branch name for recovery
- Instance terminated; EBS volume deleted (unless `persist_volume=true`)

---

## 9. Cross-References

- **DATA_MODEL.md** — Entity definitions (added: Device, WorkspaceTemplate, WorkspaceInstance, Session, SyncMetadata), migrations, revision semantics
- **API_CONTRACT.md** — Endpoint groups (added: devices, companion, workspaces), schemas, auth
- **BACKEND_IMPLEMENTATION_PLAN.md** — Updated workstreams (added: Companion, Workspaces), dependency graph, critical path
- **COMPATIBILITY_FINDINGS.md** — Repository-specific changes (added: companion protocol, workspace provisioning)
- **OPEN_DECISIONS.md** — Updated blocking uncertainties

---

## 10. Decision Log (Extended)

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-10-18 | EC2 over Lambda/ECS | Persistent worker + websocket support; simpler debugging; cost-predictable |
| 2026-10-18 | RDS PostgreSQL over DynamoDB | Relational domain; ACID; existing SQLAlchemy skill |
| 2026-10-18 | SQS FIFO over EventBridge/SNS | Exactly-once for capture processing; ordering per capture_id |
| 2026-10-18 | No Git live sync | Complexity/benefit unfavorable; export/import suffices for v1 |
| 2026-10-18 | Cognito over custom auth | Managed MFA, token refresh, social providers |
| 2026-10-18 | Core extraction pipeline preserved | 189 tests pass; typed action boundary prevents LLM→filesystem |
| 2026-10-18 | Hybrid local+AWS architecture | Local machine is primary workspace; AWS is coordination plane |
| 2026-10-18 | Outbound-only companion connection | No inbound ports; security by design |
| 2026-10-18 | Git for source code sync | Proven, handles merges, no FS replication |
| 2026-10-18 | Capability-based RPC for remote access | Least privilege; explicit approval; audit trail |
| 2026-10-18 | Devcontainer/Nix for workspace templates | Reproducible, project-specific, no baked secrets |
| 2026-10-18 | Phased milestones (M1-M5) | Portal first; advanced capabilities later |
