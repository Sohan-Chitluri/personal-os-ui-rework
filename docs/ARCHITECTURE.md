# Deku / Pos-Deku — Consolidated Architecture Documentation

**Status**: Authoritative reference for implementation. All cross-references resolved. Obsolete/duplicate material removed.

---

## Documentation Structure

```
docs/
├── ARCHITECTURE.md              # This file — single source of truth for architecture
├── DATA_MODEL.md                # Entity definitions, migrations, repository contracts
├── API_CONTRACT.md              # Endpoint groups, schemas, auth, WebSocket, sync
├── PROTOCOL_CONTRACT.md          # Companion WebSocket JSON-RPC 2.0 contract
├── INFRASTRUCTURE.md            # AWS topology, IAM, deployment, costs
├── IMPLEMENTATION_PLAN.md       # Workstreams, milestones, critical path, gates
├── DECISIONS.md                 # Blocking decisions with recommended defaults
└── ASSUMPTIONS.md               # Architectural assumptions needing validation
```

**Removed**: Duplicate content across ARCHITECTURE_DECISIONS.md, DATA_MODEL.md, API_CONTRACT.md, AWS_INFRASTRUCTURE.md, BACKEND_IMPLEMENTATION_PLAN.md, COMPATIBILITY_FINDINGS.md, OPEN_DECISIONS.md. All material consolidated here.

---

## 1. Architecture Overview

### 1.1 Three Execution Environments

| Environment | Role | Key Components |
|-------------|------|----------------|
| **Local Machine** | Primary workspace | `pos-companion` daemon (outbound WebSocket, device registration, approved RPC) |
| **AWS Control Plane** | Persistent coordination | FastAPI on EC2, RDS PostgreSQL, SQS, S3, Cognito, Session Broker (WebSocket hub) |
| **Cloud Workspaces** | On-demand dev (Milestone 5) | EC2 instances from templates, devcontainer/Nix, Git handoff, cost controls |

### 1.2 Source-of-Truth Boundaries

| Domain | Canonical Store | Sync Mechanism |
|--------|-----------------|----------------|
| Tasks, deadlines, focus sessions, captures, processing status | PostgreSQL | LWW with safeguards (rev tokens, idempotency) |
| Source code & project history | Git (GitHub/GitLab) | Git push/pull/branch/merge |
| Uncommitted source files | Working copy where edited | Explicit handoff (Git commit/push/PR) |
| Markdown project notes, decisions, documents | PostgreSQL (`project_documents`) + Markdown export | Bidirectional sync with conflict preservation |
| Binary files & generated artifacts | S3 + PostgreSQL metadata | Explicit upload/download |
| Device state & remote sessions | PostgreSQL (`devices`, `sessions`) | Companion reports presence; TTL expiry |
| Workspace templates | S3 + PostgreSQL metadata | Versioned; per-project selection |

**No continuous bidirectional filesystem replication.** Git for source; scoped sync for operational records and Markdown.

### 1.3 Backend Structure

```
src/personal_os/
├── api/                    # FastAPI (thin HTTP handlers)
│   ├── routes/             # 14 groups: tasks, projects, captures, processing, focus, stats, files, search, export, devices, companion, workspaces, sync, sessions
│   ├── schemas/            # Pydantic models
│   ├── deps.py             # Dual auth (user + device), DB session, service injection
│   └── main.py             # App factory, middleware, lifespan
├── services/               # Domain services (business logic)
│   ├── tasks.py, projects.py, captures.py, processing.py, focus.py, stats.py, files.py, search.py
│   ├── devices.py, companion.py, workspaces.py
├── core/                   # Preserved domain logic (minimally adapted)
│   ├── capture.py, processor.py, extraction/, llm.py, llm_openrouter.py, config.py
│   └── vault.py            # REPLACED by SQLAlchemy repositories
├── db/                     # SQLAlchemy + Alembic
│   ├── models/             # 15 tables (11 core + 4 hybrid)
│   ├── repositories/       # Interfaces + implementations
│   ├── migrations/         # Alembic
│   └── session.py
├── workers/                # Background workers
│   ├── capture_worker.py, workspace_worker.py, __main__.py
├── companion/              # Local daemon (separate package)
│   ├── daemon.py, rpc/, discovery.py, auth.py
└── cli/                    # Preserved, not enhanced
```

**Key Invariants**:
- FastAPI routes = zero business logic (validation, auth, serialization, delegation only)
- Services = all business logic (no HTTP, no DB session management)
- `core/extraction/` = single source of truth for semantic extraction
- `core/processor.py` adapted: `BackgroundProcessor` → worker message handler; queue = SQS
- `core/vault.py` replaced by SQLAlchemy repositories

---

## 2. Data Model (15 Tables)

### Core Tables (11)
- `users` — Cognito-synced
- `projects` — Nesting via `parent_id`, progress, tone, revision
- `tasks` — CRUD, deadlines, status transitions, mood, frog, revision
- `captures` — Immutable raw text, timestamp, source, processing status
- `processing_jobs` — Idempotency key, status, retries, DLQ
- `extracted_items` — Semantic layer (type, project, deadline, certainty, action)
- `focus_sessions` — Timer mode, duration, linked task, daily aggregates
- `project_documents` — 8 kinds (readme, progress, tasks, notes, architecture, decisions, research, timeline)
- `files` — S3 metadata (presigned URLs, metadata)
- `sync_metadata` — Offline reconciliation (idempotency key, base revision, client timestamp)
- `task_history` — Completion audit trail

### Hybrid Tables (4)
- `devices` — Companion registry (device_id, platform, capabilities, status, last_seen_at)
- `workspace_templates` — S3 path, type (devcontainer/nix/script), defaults
- `workspace_instances` — Provisioned EC2, GitHub repo, status, cost tracking, connection info
- `sessions` — Remote terminal (companion/cloud), capabilities, token, expiry
- `session_audit` — Immutable RPC audit log

### Cross-Cutting
- All mutable tables: `revision` (int), `updated_at` (timestamptz) with trigger
- Optimistic locking via `If-Match: <revision>` header
- Soft delete (`deleted_at`) for all operational records
- FTS via `tsvector` + GIN on tasks, captures, project_documents

### Repository Interfaces (Contract for Services)
```python
TaskRepository: create, get, list_by_project, update(expected_revision), delete(expected_revision), get_history
ProjectRepository: create, get, list_roots, list_children, update(expected_revision), get_documents
CaptureRepository: create, get, list_recent, update_processing_status
ProcessingJobRepository: create, claim_next(FOR UPDATE SKIP LOCKED), complete
DeviceRepository: register, get, list_by_user, update_heartbeat, revoke
WorkspaceRepository: create_instance, get, list_by_user, update_status
```

---

## 3. API Contract

### Conventions
- Base: `/api/v1` | Auth: Bearer Cognito JWT (user + device) | IDs: ULID as UUID
- Errors: RFC 7807 | Pagination: cursor (`after`, `limit`) | Optimistic lock: `If-Match`
- Rate limit: 100 req/min/user

### 14 Endpoint Groups

| Group | Path | Key Operations |
|-------|------|----------------|
| Tasks | `/tasks` | CRUD, complete, archive, history |
| Projects | `/projects` | CRUD, hierarchy, documents |
| Captures | `/captures` | Submit (persist-first), list, extractions |
| Processing | `/processing` | Job status, retry |
| Focus | `/focus/sessions` | Start, update, complete, daily aggregate |
| Stats | `/stats` | Overview, activity grid, streaks, breakdown, flavor mix |
| Files | `/files` | Presign upload/download, register, list |
| Search | `/search` | Full-text (tasks, captures, documents) |
| Export/Import | `/export`, `/import` | Project zip, all-data zip, import zip |
| Devices | `/devices` | Register, list, update, revoke, capabilities |
| Companion | `/companion/sessions`, `/companion/devices/{id}/rpc/manifest` | Session create, RPC manifest |
| Workspaces | `/workspaces` | Provision, list, get, terminate, logs, templates |
| Sync | `/sync/operations`, `/sync/status`, `/sync/conflicts/{id}/resolve` | Offline batch, conflicts, resolution |
| Sessions | `/sessions`, `/sessions/audit` | List, revoke, audit log |

### Auth
- User JWT: `sub` → `user_id`, `cognito:groups` → roles
- Device JWT: `device_id`, `user_id`, `capabilities` (Cognito device tracking enabled)
- Dual auth deps: `get_current_user`, `get_current_device`

### WebSocket Endpoints
- `/ws/companion` — JSON-RPC 2.0 (companion ↔ session broker)
- `/ws/workspace/{instance_id}` — xterm.js (portal ↔ cloud workspace)
- `/ws/realtime` — Real-time updates (v2)

### Sync Contract
- Client queues: `operation_id` (ULID), `client_updated_at`, `device_id`, `base_revision`, `payload`
- Server: idempotency check → revision match → apply | LWW (if server time ≥ 5min later + safe op) | conflict record
- Safe ops: task complete, title edit, deadline extension, focus session log
- Conflict record: server state + client payload → UI resolution

---

## 4. Infrastructure

### Deployment Topology
```
CloudFront → ALB → EC2 ASG (3 roles: API+Broker, Capture Worker, Workspace Worker)
                    ↓
            RDS PostgreSQL (Multi-AZ prod) + SQS (FIFO captures, Standard workspace/export)
                    ↓
            S3 (frontend, files, exports, templates) + Cognito (device tracking enabled)
```

### EC2 Roles (ASG min=1, max=3)
| Role | Instance | Services | Ports |
|------|----------|----------|-------|
| API + Session Broker | t3.med / t3.large | FastAPI (8000), WS Broker (8001) | 8000, 8001 |
| Capture Worker | t3.medium | `capture_worker` (SQS FIFO) | — |
| Workspace Worker | t3.medium (spot) | `workspace_worker` (SQS Std) | — |

### Key AWS Resources
- **RDS**: PostgreSQL 16, db.t3.micro (dev) / db.t3.small (prod), Multi-AZ prod
- **S3**: 4 buckets (frontend, files, exports, templates) — versioned, CORS, lifecycle
- **SQS**: FIFO (captures), Standard (export, workspace) — DLQs, content-based dedup
- **Cognito**: Device tracking enabled, hosted UI, device JWTs with `device_id` claim
- **Secrets Manager**: 6 secrets (DB, OpenRouter, Cognito, GitHub, companion JWT, session broker JWT)
- **IAM**: `PosDekuEC2Role` (RDS, SQS, S3, Secrets, SSM, EC2 for workspace), `PosDekuWorkspaceInstanceRole` (SSM, Secrets, S3)

### Cost Estimate (us-east-1, monthly)
| Component | Dev | Prod |
|-----------|-----|------|
| EC2 (3 roles) | ~$70 | ~$100 |
| RDS | $15 | $45 |
| S3 + CloudFront | ~$7 | ~$25 |
| Other (SQS, Cognito, Secrets, CW) | ~$7 | ~$30 |
| **Total** | **~$99** | **~$200** |

---

## 5. Conflict Resolution & Sync

### Default: LWW with Safeguards
Every mutable record: `id` (ULID), `updated_at` (server), `revision` (monotonic), `device_id`

**Online**: Optimistic lock via `If-Match` → 409 on mismatch

**Offline**: Client queues ops with `operation_id`, `client_updated_at`, `device_id`, `base_revision`
- Server validates monotonicity per device
- Base revision matches → apply
- Differs → LWW **only if**: server time ≥ 5min later **AND** op in safe list (complete, title edit, deadline extend, focus log)
- Otherwise → `conflict_record` for UI resolution
- Idempotency: `operation_id` unique constraint

### Per-Type Policies
| Data Type | Policy |
|-----------|--------|
| Task fields (title, estimate, mood, frog) | LWW safe |
| Task completion toggle | LWW safe (idempotent) |
| Task deadline | LWW if server time clearly later |
| Task project move | **Conflict** (structural) |
| Project hierarchy | **Conflict** |
| Markdown documents | Per-document conflict preservation (both versions retained) |
| Captures | Always append (immutable) |
| Focus sessions | LWW safe |
| Device presence | Server TTL wins |
| Workspace state | Server wins (ephemeral) |

---

## 6. Security Model

### Companion ↔ AWS
- Outbound-only WebSocket to session broker (port 8001)
- JWT device credentials (Cognito device tracking) — **not mTLS**
- Session broker validates, multiplexes JSON-RPC 2.0

### Capability-Based RPC
| Capability | Methods | Authorization |
|------------|---------|---------------|
| `terminal.read` | `pty_read`, `pty_list` | User + device approval |
| `terminal.write` | `pty_write`, `pty_resize` | User + device approval |
| `terminal.control` | `pty_spawn`, `pty_kill` | User + device + MFA |
| `filesystem.read` | `fs_read`, `fs_list`, `fs_stat` | Path allowlist (projects only) |
| `filesystem.write` | `fs_write`, `fs_mkdir` | Path allowlist + confirmation |
| `process.list` | `ps_list` | User + device |
| `process.exec` | `ps_exec` | **Explicit per-command approval** + allowlist |
| `agent.orchestrate` | `agent_spawn`, `agent_status` | User + device (Hermes) |

### Session Lifecycle
1. Portal requests capability → AWS checks device online + user approval
2. Short-lived session token (15 min default, renewable to 4h)
3. Portal opens WebSocket with token
4. Broker forwards to companion; companion validates token + capability
5. All RPC calls logged to `session_audit` (redacted params)
6. End on: close, expiry, device offline, revocation

---

## 7. Cloud Workspaces (Milestone 5)

### Provisioning Flow
`POST /workspaces` → SQS → `workspace_worker` → `EC2 RunInstances` → UserData (clone, template, tools) → `workspace_instances` → WS URL

### Templates (S3 `pos-deku-templates-{env}`)
- `devcontainer.json` (primary) — VS Code Dev Container spec
- `flake.nix` — Nix flake
- `setup.sh` — Fallback script

### Instance Security
- Role: `PosDekuWorkspaceInstanceRole` (SSM, Secrets, S3 templates/files)
- SG: Outbound only (GitHub, S3, Secrets, PyPI)
- Access: SSM Session Manager or portal WebSocket only

### Controls
- Max 2 concurrent/user (configurable)
- Idle timeout: 30 min default (configurable 15-120)
- Max runtime: 4 hours default (configurable 1-8h)
- Cost limit: $5 default (user-overridable to $50)
- Idle/max enforced by worker + CloudWatch alarm backup

### Handoff
On terminate: Git status → push uncommitted to `workspace/<instance_id>/<ISO_timestamp>` branch → notify user

---

## 8. Key Architectural Decisions (Locked)

| Decision | Rationale |
|----------|-----------|
| EC2 over Lambda/ECS | Persistent worker + WebSocket; simpler debugging; predictable cost |
| RDS PostgreSQL over DynamoDB | Relational domain; ACID; existing SQLAlchemy skill |
| SQS FIFO for captures | Exactly-once; ordering per capture_id |
| No Git live sync | Complexity/benefit unfavorable; export/import suffices |
| Cognito over custom auth | Managed MFA, token refresh, social providers |
| Core extraction preserved | 189 tests pass; typed action boundary prevents LLM→filesystem |
| Hybrid local+AWS | Local machine primary; AWS = coordination plane |
| Outbound-only companion | No inbound ports; security by design |
| Git for source sync | Proven, handles merges, no FS replication |
| Capability-based RPC | Least privilege; explicit approval; audit trail |
| Devcontainer primary | Best IDE integration; Nix/script fallback |
| Phased milestones (M1-M5) | Portal first; advanced capabilities later |

---

## 9. Cross-References
- **DATA_MODEL.md** — Full entity definitions, migrations, repository contracts
- **API_CONTRACT.md** — Full endpoint schemas, auth, WebSocket, sync
- **INFRASTRUCTURE.md** — Terraform topology, IAM, costs, deployment
- **IMPLEMENTATION_PLAN.md** — Workstreams, milestones, critical path
- **DECISIONS.md** — Blocking decisions with defaults
- **ASSUMPTIONS.md** — Architectural assumptions needing validation
