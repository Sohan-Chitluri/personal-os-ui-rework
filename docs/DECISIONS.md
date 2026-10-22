# Blocking Decisions — Deku / Pos-Deku

**Only genuinely blocking architectural decisions requiring your input.**  
Routine engineering choices (library versions, naming conventions, etc.) are delegated to workstream owners.

Each decision has a **recommended default**. Respond with `OD-XXX: [CHOICE] — [rationale if not default]` only if you disagree.

---

## Week 1 — Infrastructure & Compute (Before Any Code)

### OD-001: Compute Platform — EC2
**Default**: EC2 (t3.medium dev / t3.large prod) with ASG + systemd  
**Alternatives**: ECS Fargate, ECS EC2  
**Impact**: Workstream F (Infrastructure), deployment scripts, worker management  
**Recommendation**: EC2 — persistent WebSocket, simpler debugging, predictable cost

### OD-002: RDS Instance Sizing
**Default**: `db.t3.micro` (dev) / `db.t3.small` (prod, Multi-AZ)  
**Alternatives**: Larger dev, Aurora Serverless v2  
**Impact**: Workstream F (Terraform), cost  
**Recommendation**: Start small; add CW alarms (CPU>70%, FreeMem<200MB) for auto-scale trigger

### OD-003: SQS Queue Type for Captures
**Default**: Standard SQS with database-backed idempotency and a redrive DLQ
**Alternative**: FIFO where per-user ordering becomes a measured requirement
**Impact**: Workstream D (Worker), F (Terraform)
**Recommendation**: Standard SQS. Delivery is at least once; processing
idempotency comes from the database. SQS FIFO deduplication does not provide
exactly-once database effects and should not be described as doing so.

### OD-004: Cognito Authentication UX
**Default**: Hosted UI (`auth.pos-deku.com`)  
**Alternative**: Custom frontend auth forms  
**Impact**: Workstream C (Auth), E (Frontend), F (Cognito config)  
**Recommendation**: Hosted UI for v1 — fastest to implement, managed MFA

### OD-008: TUI/CLI Preservation
**Default**: Deprecate TUI for v1; thin API client for CLI (`pos` calls HTTP API)  
**Alternatives**: Dual-write, local-first TUI, full CLI rewrite  
**Impact**: Workstream B (if CLI changes), C (API design), E (parity)  
**Recommendation**: Deprecate TUI; CLI via API if workflow critical

---

## Week 2 — Sync, Conflict, Migration (Before M1)

### OD-006: Conflict Resolution Default
**Default**: LWW with safeguards (safe ops: task complete, title edit, deadline extend, focus log)  
**Alternatives**: Server-wins, client-wins, CRDT  
**Impact**: Workstream A (conflict function), C (Sync routes), E (Offline queue UI)  
**Recommendation**: LWW with 5-min server-time margin + explicit safe-op list

### OD-007: Offline Sync Scope v1
**Default**: Cloud canonical; offline queue reconciled on reconnect (no background sync)  
**Alternatives**: Background sync, optimistic UI only  
**Impact**: Workstream C (Sync routes), E (Frontend store), A (sync_metadata)  
**Recommendation**: As specified — flush on `online` event

### OD-010: Database Migration Policy
**Default**: Additive-only for v1 (nullable columns, defaults, async backfill)  
**Alternatives**: Blue-green, maintenance window  
**Impact**: Workstream A (migrations), F (deployment), G (migration tests)  
**Recommendation**: Additive-only; document policy for breaking changes

### OD-012: Timezone Handling
**Default**: Client sends IANA tz in capture/task create; fallback to user profile (`preferred_timezone`)  
**Alternatives**: Server infers from timestamp, UTC only  
**Impact**: Workstream A (models), B (deadline normalization), C (API), E (Frontend)  
**Recommendation**: Client sends tz + profile default

### OD-016: Frontend Data Shape Mapping
**Default**: Adapter layer in `blockday-api.ts` (frontend converts `deadline`↔`dueIn`, `start_minute`↔`start`)  
**Alternatives**: Backend adds computed fields, unify on backend shape  
**Impact**: Workstream E (Frontend), C (API schemas)  
**Recommendation**: Adapter layer — keeps backend canonical

### OD-018: Session Token TTL
**Default**: 15 min default, renewable to 4h with activity  
**Alternatives**: 5 min, 30 min  
**Impact**: Workstream H (Companion auth), C (Session routes), F (Broker config)  
**Recommendation**: 15 min + renewal

### OD-019: Companion → Broker Auth
**Default**: JWT (Cognito device tracking + custom claims)  
**Alternative**: mTLS  
**Impact**: Workstream H (Companion auth), F (Broker config), C (Device auth)  
**Recommendation**: JWT — leverages managed device tracking; simpler ops

### OD-028: Device Auth Method
**Default**: Cognito device tracking enabled  
**Alternative**: Custom device registry  
**Impact**: Workstream C (Auth), H (Companion), F (Cognito config)  
**Recommendation**: Cognito device tracking — managed, integrates with hosted UI

### OD-030: Markdown Conflict Preservation
**Default**: Store both versions on conflict; UI shows 3-way merge (base/server/client)  
**Alternatives**: LWW only, Operational Transform  
**Impact**: Workstream A (conflict function), C (Sync routes), E (Conflict UI)  
**Recommendation**: Store both; 3-way merge UI

---

## Week 3 — Companion & Workspaces (Before M2/M5)

### OD-005: Export Format
**Default**: Zip download (in-memory, `FileResponse`)  
**Alternatives**: Streaming tar.gz, per-file endpoints  
**Impact**: Workstream C (Export route), E (Frontend download)  
**Recommendation**: Zip for v1 — simple, browser-compatible

### OD-011: Search Backend
**Default**: PostgreSQL FTS only (tsvector + GIN)  
**Alternatives**: Tantivy (local dev / embedded)  
**Impact**: Workstream A (indexes), C (Search route), F (if Tantivy needs service)  
**Recommendation**: PG FTS only for v1 — simpler, single dependency

### OD-013: Voice Capture Pipeline
**Default**: Defer to post-v1 (local CLI/TUI only)  
**Alternatives**: S3 + OpenAI Whisper API, self-hosted Whisper on EC2  
**Impact**: Workstream C (Files + Captures), D (Worker), F (S3 + GPU)  
**Recommendation**: Defer — document as known gap

### OD-014: S3 CORS Domain
**Default**: `https://app.pos-deku.com` (specific origin)  
**Alternatives**: `*`, Lambda@Edge dynamic  
**Impact**: Workstream F (S3 CORS), E (Frontend upload)  
**Recommendation**: Specific origin; CloudFront Functions for header injection if needed

### OD-017: Companion RPC Capabilities (v1)
**Default**: Minimal — `terminal.read`, `terminal.write`, `filesystem.read`, `filesystem.write`, `process.list`  
**Deferred**: `terminal.control`, `process.exec`, `agent.orchestrate`  
**Impact**: Workstream H (RPC), C (Companion routes), H (RPC impl)  
**Recommendation**: Minimal v1 — covers 80% remote dev needs

### OD-020: Workspace Template Format
**Default**: Devcontainer.json primary; Nix flake + setup.sh fallback  
**Alternatives**: Nix primary, all equal  
**Impact**: Workstream I (Templates), F (S3 bucket layout)  
**Recommendation**: Devcontainer primary — best IDE integration

### OD-021: GitHub Auth for Workspace Provisioning
**Default**: GitHub App (per-installation tokens, 1h TTL)  
**Alternatives**: PAT per user, SSH deploy keys  
**Impact**: Workstream I (Worker), F (Secrets Manager, IAM)  
**Recommendation**: GitHub App — scoped, auto-expiring, revocable

### OD-022: Max Concurrent Workspaces
**Default**: 2 per user (configurable via tier later)  
**Alternatives**: 5, configurable per tier  
**Impact**: Workstream I (WorkspaceService), C (Workspaces API), F (CW quota alarms)  
**Recommendation**: 2 default — conservative

### OD-023: Idle Timeout for Workspaces
**Default**: 30 min default (user-configurable 15-120 min)  
**Alternatives**: 60 min, 15 min  
**Impact**: Workstream I (Worker), C (Workspaces API), F (CW alarm)  
**Recommendation**: 30 min — standard for dev environments

### OD-024: Max Runtime for Workspaces
**Default**: 4 hours default (user-configurable 1-8h)  
**Alternatives**: 8h, 2h  
**Impact**: Workstream I (Worker), C (Workspaces API), F (CW alarm)  
**Recommendation**: 4h — typical deep-work session

### OD-025: Spot Instances for Workspace Worker
**Default**: Yes (SQS visibility timeout 5min covers interruption)  
**Alternative**: On-demand only  
**Impact**: Workstream F (ASG config), D (Worker graceful shutdown)  
**Recommendation**: Yes — 60-90% savings; SQS handles interruption

### OD-026: Cost Limit Default per Workspace
**Default**: $5.00 (user-overridable to $50)  
**Alternatives**: $10, $20  
**Impact**: Workstream I (WorkspaceService), C (Workspaces API), F (CW billing alarm)  
**Recommendation**: $5 — ~4h on t3.medium; conservative

### OD-027: Recovery Branch Naming
**Default**: `workspace/<instance_id>/<ISO_timestamp>` (e.g., `workspace/01JABC123/2026-10-18T14:30:00Z`)  
**Alternatives**: Short ID, user-namespaced  
**Impact**: Workstream I (Git handoff), C (Terminate response)  
**Recommendation**: Sortable, unique, self-documenting

### OD-029: Offline Queue Storage
**Decision**: Browser queue uses IndexedDB. The companion has no local queue and submits operations through the authenticated cloud protocol when connected.  
**Future option**: If disconnected companion edits need durable storage, evaluate a local PostgreSQL append-only outbox; cloud PostgreSQL remains canonical.  
**Impact**: Workstream E (browser queue), H (authenticated companion protocol)  
**Status**: Resolved by the M3 storage correction; no SQLite companion queue.

---

## Decision Tracking

| ID | Decision | Status | Deadline |
|----|----------|--------|----------|
| OD-001 | Compute: EC2 | Pending | Week 1 |
| OD-002 | RDS sizing | Pending | Week 1 |
| OD-003 | SQS FIFO | Pending | Week 1 |
| OD-004 | Cognito Hosted UI | Pending | Week 1 |
| OD-008 | TUI/CLI strategy | Pending | Week 1 |
| OD-006 | Conflict LWW | Pending | Week 2 |
| OD-007 | Offline scope | Pending | Week 2 |
| OD-010 | Migration policy | Pending | Week 2 |
| OD-012 | Timezone handling | Pending | Week 2 |
| OD-016 | Frontend data mapping | Pending | Week 2 |
| OD-018 | Session token TTL | Resolved (15 min default + renewal) | Week 2 |
| OD-019 | mTLS vs JWT | Resolved (JWT / verifiable device token) | Week 2 |
| OD-028 | Device auth | Resolved (Registered device credentials + user ownership) | Week 2 |
| OD-030 | Markdown conflict preservation | Pending | Week 2 |
| OD-005 | Export zip | Pending | Week 3 |
| OD-011 | Search backend | Pending | Week 3 |
| OD-013 | Voice capture v1 | Pending | Week 3 |
| OD-014 | S3 CORS domain | Pending | Week 3 |
| OD-017 | Companion RPC capabilities | Resolved (v1 safe set: process.list, fs.discover, fs.stat, system.ping) | Week 3 |
| OD-020 | Workspace template format | Pending | Week 3 |
| OD-021 | GitHub auth | Pending | Week 3 |
| OD-022 | Max concurrent workspaces | Pending | Week 3 |
| OD-023 | Idle timeout | Pending | Week 3 |
| OD-024 | Max runtime | Pending | Week 3 |
| OD-025 | Spot instances | Pending | Week 3 |
| OD-026 | Cost limit | Pending | Week 3 |
| OD-027 | Recovery branch naming | Pending | Week 3 |
| OD-029 | Offline queue storage | Resolved: browser IndexedDB; no companion queue | M3 storage correction |

---

## Resolution Format

For each decision, respond with:
```
OD-XXX: [CHOICE] — [Brief rationale if not default]
```

Example:
```
OD-001: EC2 confirmed — Team has EC2 ops experience.
OD-004: Custom UI — Design team requires branded auth flow.
OD-013: Defer voice — Not in v1 scope per roadmap.
```

**Only respond to decisions where you disagree with the default.** Silence = accept default.
