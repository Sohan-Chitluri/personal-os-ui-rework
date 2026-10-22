# Open Decisions — Deku / Pos-Deku Backend

**Only genuinely blocking uncertainties that cannot be resolved from the repository.**  
Each item requires a decision before the associated workstream can proceed.

---

## OD-001: Compute Platform — EC2 vs. ECS/Fargate

**Status**: Locked as EC2 in ARCHITECTURE_DECISIONS.md, but requires confirmation.

**Context**: 
- Architecture doc specifies EC2 for persistent worker + websocket support.
- ECS Fargate would eliminate instance management but adds complexity for long-running worker processes and websockets.

**Options**:
1. **EC2 (locked)** — t3.medium, ASG, systemd services. Simple, predictable cost, full control.
2. **ECS Fargate** — Task definitions for API + worker. Serverless, but worker needs long-running task support; websockets require ALB + sticky sessions.
3. **ECS EC2** — Hybrid: managed orchestration on EC2 capacity.

**Impact**: Affects Workstream F (Infrastructure), deployment scripts, worker process management.

**Recommendation**: Confirm EC2. Re-evaluate only if team has strong ECS expertise and wants to avoid instance patching.

**Decision Required**: Yes / No to EC2. If No, specify alternative.

---

## OD-002: RDS Instance Sizing

**Status**: Specified as `db.t3.micro` (dev) / `db.t3.small` (prod) in AWS_INFRASTRUCTURE.md.

**Context**: 
- Dev: Low traffic, single developer. `db.t3.micro` (2 vCPU, 1 GB) ~$15/mo.
- Prod: Unknown load. `db.t3.small` (2 vCPU, 2 GB) ~$45/mo Multi-AZ.
- PostgreSQL memory pressure depends on connection pool size, work_mem, shared_buffers.

**Options**:
1. **As specified** — Start small, monitor, scale vertically.
2. **Larger dev** — `db.t3.small` for dev to match prod behavior.
3. **Serverless v2** — Aurora Serverless v2 for automatic scaling (higher base cost).

**Impact**: Workstream F (Terraform), cost estimation.

**Recommendation**: Start as specified. Add CloudWatch alarm on CPU > 70% and FreeableMemory < 200MB to trigger resize.

**Decision Required**: Confirm instance classes or specify alternatives.

---

## OD-003: SQS Queue Type — FIFO vs Standard

**Status**: Locked as FIFO for capture processing in ARCHITECTURE_DECISIONS.md.

**Context**: 
- FIFO guarantees ordering per `MessageGroupId` (use `user_id` or `capture_id`) and exactly-once via content-based deduplication.
- Standard queue: higher throughput, at-least-once, no ordering.

**Options**:
1. **FIFO (locked)** — `MessageGroupId = user_id`; content-based dedup on `idempotency_key`. Throughput: 300 msg/s (3000 with batching).
2. **Standard + App-level dedup** — Higher throughput, but worker must handle duplicates via `idempotency_key` in DB (already required).

**Impact**: Workstream D (Worker), F (Terraform).

**Recommendation**: FIFO for capture processing (correctness > throughput). Standard for export jobs (low volume, idempotent anyway). Standard for workspace provisioning (lower throughput).

**Decision Required**: Confirm FIFO for capture processing queue.

---

## OD-004: Cognito Authentication UX — Hosted UI vs Custom

**Status**: Locked as Cognito Hosted UI in ARCHITECTURE_DECISIONS.md.

**Context**: 
- Hosted UI: Cognito handles login/signup/MFA pages. Custom domain (`auth.pos-deku.com`). Minimal frontend code.
- Custom UI: Frontend builds login forms, calls Cognito APIs directly. Full control over UX.

**Options**:
1. **Hosted UI (locked)** — Redirect to `https://auth.pos-deku.com/login`. Callback to `https://app.pos-deku.com/callback`. Fastest to implement.
2. **Custom UI** — Frontend owns auth forms. More work, but seamless UX.

**Impact**: Workstream C (Auth), E (Frontend integration), F (Cognito domain config).

**Recommendation**: Hosted UI for v1. Migrate to custom if UX demands it.

**Decision Required**: Confirm Hosted UI or choose Custom.

---

## OD-005: Markdown Export Format — Zip vs Streaming API

**Status**: Locked as zip download in API_CONTRACT.md §3.9.

**Context**: 
- Zip: Single download, contains all `.md` files. Simple, works with browser download.
- Streaming: Server streams tar/gzip or individual files. Better for large exports, but more complex.

**Options**:
1. **Zip (locked)** — `ExportService` creates in-memory zip, returns `FileResponse`. Max ~50MB (projects + captures).
2. **Streaming tar.gz** — Chunked response, lower memory.
3. **Per-file endpoints** — Client downloads each file.

**Impact**: Workstream C (Export route), E (Frontend download handling).

**Recommendation**: Zip for v1. Stream if exports exceed 100MB.

**Decision Required**: Confirm zip or specify alternative.

---

## OD-006: Conflict Resolution Default Policy

**Status**: Specified as LWW with safeguards in ARCHITECTURE_DECISIONS.md §4.

**Context**: 
- Current repo: Single-user, no conflicts.
- Multi-device: Offline edits need reconciliation.
- LWW with safeguards: Server time wins for safe ops; conflicts preserved for structural changes.

**Options**:
1. **LWW with safeguards (locked)** — As documented. Simple, predictable.
2. **Operational Transform / CRDT** — Complex, overkill for v1.
3. **Server-wins always** — Simpler but loses offline edits silently.
4. **Client-wins always** — Risks data loss on server.

**Impact**: Workstream A (conflict function), C (Sync routes), E (Offline queue UI).

**Recommendation**: LWW with safeguards as documented. Explicitly list safe operations (task complete, title edit, deadline extend).

**Decision Required**: Confirm policy or propose modification to safe-operation list.

---

## OD-007: Offline Sync Scope v1

**Status**: "Cloud-backed canonical, offline queue reconciled on reconnect" in ARCHITECTURE_DECISIONS.md.

**Context**: 
- Full multi-master replication is explicitly excluded.
- v1 scope: User edits offline → queued locally → submitted on reconnect → server reconciles.
- No peer-to-peer, no background sync while online.

**Options**:
1. **As specified (locked)** — Online = direct API. Offline = local queue + manual/auto flush on reconnect.
2. **Background sync** — Service worker syncs periodically when online. More complex.
3. **Optimistic UI only** — No offline queue; fail fast if offline. Simpler but worse UX.

**Impact**: Workstream C (Sync routes), E (Frontend store + offline queue), A (sync_metadata).

**Recommendation**: As specified. Implement local queue in frontend (IndexedDB), flush on `online` event.

**Decision Required**: Confirm scope or request background sync.

---

## OD-008: TUI/CLI Preservation Level

**Status**: "Preserve where practical, no enhancements" in BACKEND_IMPLEMENTATION_PLAN.md.

**Context**: 
- Current TUI/CLI uses `VaultManager` (filesystem+git).
- New backend uses PostgreSQL + API.
- TUI/CLI would need rewrite to use API, or keep local vault (diverged).

**Options**:
1. **Deprecate TUI/CLI for v1** — Focus on web. Keep code but mark deprecated. Simplest.
2. **Thin API client for CLI** — `pos` CLI calls HTTP API. Preserves CLI workflow.
3. **Dual-write** — TUI/CLI writes to local vault AND calls API. Complex.
4. **Local-first TUI** — TUI stays on local vault; sync to cloud via background process. Architecturally distinct.

**Impact**: Workstream B (Core Adaptation — if CLI needs changes), C (API design), E (Frontend parity).

**Recommendation**: Option 1 (deprecate) for v1. Option 2 (CLI via API) if CLI is critical for user workflow. No TUI work.

**Decision Required**: Choose preservation strategy. If CLI via API, add to Workstream C scope.

---

## OD-009: Real LLM Integration Testing in CI

**Status**: Not specified. Audit found `OPENROUTER_API_KEY` required for semantic path.

**Context**: 
- `test_semantic_pipeline.py` mocks LLMProvider. Real integration untested.
- OpenRouter API costs money; rate limits exist.
- CI needs to validate semantic extraction works end-to-end.

**Options**:
1. **Mock only (current)** — No real LLM in CI. Low confidence in prompt changes.
2. **Nightly real LLM test** — Run with real `OPENROUTER_API_KEY` on schedule. Cost ~$5-10/mo.
3. **PR-gated real test** — Run on `test-semantic` label. Manual trigger.
4. **Local LLM (llama.cpp)** — Run local model in CI. Free but slow, different behavior.

**Impact**: Workstream B (Core Adaptation), G (Integration).

**Recommendation**: Option 3 (PR-gated) for v1. Add `pytest --run-real-llm` marker.

**Decision Required**: Choose testing strategy. If real LLM, approve budget and provide API key for CI.

---

## OD-010: Database Migration Strategy for Schema Evolution

**Status**: Alembic specified, but production migration policy not defined.

**Context**: 
- v1 ships with initial schema.
- Future changes: additive (new columns), breaking (renames, type changes).
- RDS requires careful migration to avoid downtime.

**Options**:
1. **Additive only for v1** — No breaking changes. New columns nullable or with defaults. Backfill async.
2. **Blue-green migrations** — Duplicate tables, swap, drop old. Zero downtime but complex.
3. **Maintenance window** — Accept brief downtime for breaking changes.

**Impact**: Workstream A (migrations), F (deployment), G (migration tests).

**Recommendation**: Option 1 for v1. Document policy: "All migrations must be backward-compatible. Breaking changes require blue-green or maintenance window."

**Decision Required**: Confirm additive-only policy for v1.

---

## OD-011: Search Backend — PostgreSQL FTS vs Tantivy

**Status**: Both mentioned in DATA_MODEL.md §8.

**Context**: 
- PostgreSQL FTS: Built-in, ACID, good for structured + full-text. `tsvector` + GIN index.
- Tantivy: Rust-based, faster, more features (fuzzy, highlighting), separate process or embedded.

**Options**:
1. **PostgreSQL FTS only (v1)** — Simpler, single dependency. Good enough for task/capture search.
2. **Tantivy for local dev, PG FTS for prod** — Complexity.
3. **Tantivy embedded in worker** — Async indexing, separate from DB.

**Impact**: Workstream A (indexes), C (Search route), F (if Tantivy needs separate service).

**Recommendation**: PostgreSQL FTS only for v1. Evaluate Tantivy if search quality insufficient.

**Decision Required**: Confirm PG FTS only or approve Tantivy integration.

---

## OD-012: Timezone Handling

**Status**: Not explicitly decided. `ExtractionContext` has `timezone` field (IANA).

**Context**: 
- Frontend sends `timezone` in capture submit? Or infer from `timestamp`?
- Deadline normalization (`due_in` days) depends on user's "today".
- Server stores `timestamptz` (UTC). Conversion at read time.

**Options**:
1. **Client sends IANA tz** — Frontend includes `timezone` in capture/task create. Server uses for `due_in` computation.
2. **Server infers from timestamp** — `timestamp` has offset; derive "today" boundary. Fails for future-dated tasks.
3. **User profile setting** — Store preferred timezone in `users` table. Default to UTC.

**Impact**: Workstream A (models), B (deadline normalization), C (API), E (Frontend).

**Recommendation**: Option 1 (client sends tz) + Option 3 (profile default). Capture API accepts optional `timezone`; defaults to user profile.

**Decision Required**: Confirm approach.

---

## OD-013: Voice Capture Pipeline in v1

**Status**: `skills/voice_capture.py` exists, uses `whisper.cpp` locally.

**Context**: 
- Current: Local recording → local whisper → `capture_and_process()`.
- Cloud: Recording uploaded to S3 → transcription worker (Whisper API or self-hosted) → capture API.

**Options**:
1. **Defer to post-v1** — Voice capture via local CLI/TUI only. Web UI gets "upload audio" later.
2. **S3 + OpenAI Whisper API** — Upload → Lambda/Worker calls Whisper API → capture API.
3. **Self-hosted Whisper on EC2** — Worker runs whisper.cpp. No API cost, more ops.

**Impact**: Workstream C (Files + Captures), D (Worker), F (S3 + maybe GPU instance).

**Recommendation**: Defer to post-v1. Document as known gap.

**Decision Required**: Confirm defer or approve v1 scope.

---

## OD-014: CORS Policy for S3 Presigned URLs

**Status**: CORS config specified in AWS_INFRASTRUCTURE.md but domain not finalized.

**Context**: 
- Presigned PUT/GET URLs require CORS on bucket.
- Frontend domain: `app.pos-deku.com` (or similar).

**Options**:
1. **Allow specific origin** — `https://app.pos-deku.com` only.
2. **Allow all (`*`)** — Dev convenience, security risk.
3. **Dynamic origin validation** — Lambda@Edge on CloudFront.

**Impact**: Workstream F (S3 CORS), E (Frontend upload).

**Recommendation**: Option 1. Use CloudFront Functions to inject CORS headers if needed.

**Decision Required**: Confirm frontend domain for CORS config.

---

## OD-015: Monitoring / Alerting Thresholds

**Status**: Alarms listed in AWS_INFRASTRUCTURE.md but thresholds are estimates.

**Context**: 
- No baseline traffic yet. Thresholds (5xx > 1%, queue > 100) are guesses.
- Will tune after load test (Workstream G).

**Options**:
1. **Deploy with estimates** — Tune after M6 (Frontend Connected).
2. **No alarms until baseline** — Risk missing issues in staging.

**Impact**: Workstream F (CloudWatch alarms).

**Recommendation**: Deploy with estimates, mark as "tune after baseline" in runbook.

**Decision Required**: Confirm or provide baseline expectations.

---

## OD-016: Frontend Data Shape Mapping

**Status**: API schemas differ from `blockday-data.ts` (e.g., `dueIn` vs `deadline`, `start` vs `start_minute`).

**Context**: 
- Frontend `Task` has `dueIn` (days from viewed day), `start` (minutes from 06:00).
- Backend `Task` has `deadline` (date), `start_minute` (minutes).
- Frontend computes `dueIn` from `deadline` + viewed date.

**Options**:
1. **Frontend adapter layer** — `blockday-api.ts` converts on read/write. Backend unchanged.
2. **Backend adds computed fields** — API returns both `deadline` and `dueIn`.
3. **Unify on backend shape** — Frontend migrates to `deadline` + `start_minute`.

**Impact**: Workstream E (Frontend Integration), C (API schemas).

**Recommendation**: Option 1 (adapter layer). Keeps backend canonical; frontend adapts.

**Decision Required**: Confirm adapter approach.

---

## OD-017: Companion RPC Capabilities — Initial Set

**Status**: ARCHITECTURE_DECISIONS.md lists 8 capabilities but scope needs confirmation.

**Context**: 
- Companion daemon exposes RPC for terminal, filesystem, process, agent.
- Each capability requires approval model, allowlists, audit logging.
- More capabilities = more attack surface.

**Options**:
1. **Minimal v1** — `terminal.read`, `terminal.write`, `filesystem.read`, `filesystem.write`, `process.list`. Defer `terminal.control`, `process.exec`, `agent.orchestrate`.
2. **Full set** — All 8 capabilities with approval gates.
3. **Terminal-only v1** — Only `terminal.read` + `terminal.write` for remote terminal MVP.

**Impact**: Workstream H (Companion RPC), C (Companion routes), H (RPC implementation).

**Recommendation**: Option 1 (Minimal v1). Terminal + filesystem + process.list covers 80% of remote dev needs. Defer exec/agent to post-v1.

**Decision Required**: Approve initial capability set.

---

## OD-018: Session Token TTL

**Status**: ARCHITECTURE_DECISIONS.md mentions 5-15 min but default not set.

**Context**: 
- Companion sessions use short-lived tokens for WebSocket auth.
- Too short = frequent re-auth; too long = exposure window.
- Renewable via refresh endpoint.

**Options**:
1. **15 min default, renewable** — Balance of security and UX.
2. **5 min default** — Higher security, more renewals.
3. **30 min default** — Better UX, longer exposure.

**Impact**: Workstream H (Companion auth), C (Session routes), F (Session broker config).

**Recommendation**: 15 min default, renewable up to 4 hours with activity.

**Decision Required**: Confirm TTL and renewal policy.

---

## OD-019: mTLS vs JWT for Companion → Broker

**Status**: ARCHITECTURE_DECISIONS.md mentions both; need to pick one.

**Context**: 
- Companion connects outbound to session broker on EC2.
- mTLS: Stronger identity, cert management complexity.
- JWT: Simpler, uses Cognito device tracking, standard OAuth2 flow.

**Options**:
1. **JWT (locked)** — Cognito device tracking + custom claims. Simpler ops.
2. **mTLS** — Client certs issued at registration. Stronger but more infra.
3. **Both** — JWT for auth, mTLS for transport (overkill).

**Impact**: Workstream H (Companion auth), F (Session broker config), C (Device auth).

**Recommendation**: JWT. Cognito device tracking already required; cert management adds operational burden.

**Decision Required**: Confirm JWT or choose mTLS.

---

## OD-020: Workspace Template Format

**Status**: Multiple formats mentioned; need primary + fallback decision.

**Context**: 
- Devcontainer.json: VS Code standard, rich ecosystem, Docker-based.
- Nix flake: Reproducible, declarative, growing adoption.
- Setup.sh: Universal fallback, least reproducible.

**Options**:
1. **Devcontainer primary, Nix + script fallback** — Best ecosystem support.
2. **Nix primary** — Most reproducible, but smaller talent pool.
3. **All equal** — Worker tries each in order.

**Impact**: Workstream I (Workspace templates), F (S3 template bucket layout).

**Recommendation**: Option 1. Devcontainer has best IDE integration; Nix for reproducible builds.

**Decision Required**: Confirm primary format.

---

## OD-021: GitHub Authentication for Workspace Provisioning

**Status**: Need to clone private repos during workspace provisioning.

**Context**: 
- Workspace worker runs on EC2, needs GitHub access.
- GitHub App: Scoped permissions, per-installation, short-lived tokens.
- PAT: Simpler but broader scope, long-lived.

**Options**:
1. **GitHub App (locked)** — Install per user/org; worker uses installation token (1h TTL).
2. **PAT per user** — User provides PAT; stored in Secrets Manager.
3. **SSH key per workspace** — Generated at provision; added to GitHub deploy keys.

**Impact**: Workstream I (Workspace worker), F (Secrets Manager, IAM).

**Recommendation**: GitHub App. Best security posture; tokens auto-expire; revocable per installation.

**Decision Required**: Confirm GitHub App or approve PAT fallback.

---

## OD-022: Max Concurrent Workspaces Per User

**Status**: Not specified; need quota for cost control.

**Context**: 
- Each workspace = EC2 instance (~$0.05-0.10/hr).
- Unbounded = cost risk.
- Quota enforced at provisioning API.

**Options**:
1. **2 (default)** — Conservative; covers main + experimental.
2. **5** — More flexibility.
3. **Configurable per tier** — Free=1, Pro=3, Team=10.

**Impact**: Workstream I (WorkspaceService), C (Workspaces API), F (CloudWatch quota alarms).

**Recommendation**: 2 default, configurable via user tier (future).

**Decision Required**: Confirm default quota.

---

## OD-023: Idle Timeout for Cloud Workspaces

**Status**: ARCHITECTURE_DECISIONS.md says 30 min default; confirm.

**Context**: 
- Idle = no WebSocket activity + no RPC calls.
- Auto-terminate saves cost.
- Too aggressive = frustrating UX (re-provision delay).

**Options**:
1. **30 min (default)** — Standard for dev environments.
2. **60 min** — More generous.
3. **15 min** — Aggressive cost savings.

**Impact**: Workstream I (Workspace worker), C (Workspaces API), F (CloudWatch alarm).

**Recommendation**: 30 min default, user-configurable (15-120 min).

**Decision Required**: Confirm default and configurability.

---

## OD-024: Max Runtime for Cloud Workspaces

**Status**: ARCHITECTURE_DECISIONS.md says 4 hours default; confirm.

**Context**: 
- Hard limit prevents runaway instances.
- User can re-provision if needed.
- 4 hours = typical deep-work session.

**Options**:
1. **4 hours (default)** — Balances session length vs cost.
2. **8 hours** — Full work day.
3. **2 hours** — Forces breaks, lower cost.

**Impact**: Workstream I (Workspace worker), C (Workspaces API), F (CloudWatch alarm).

**Recommendation**: 4 hours default, user-configurable (1-8 hours).

**Decision Required**: Confirm default and configurability.

---

## OD-025: Spot Instances for Workspace Worker

**Status**: Not specified; workspace worker is intermittent.

**Context**: 
- Workspace worker polls SQS, provisions EC2.
- Can tolerate interruption (SQS message returns to queue).
- Spot = 60-90% cost savings.

**Options**:
1. **Yes (spot for worker)** — Worker ASG uses spot; interruption handled by SQS visibility timeout.
2. **No (on-demand)** — Simpler, no interruption handling.
3. **Mixed** — On-demand baseline + spot scaling.

**Impact**: Workstream F (ASG config), D (Worker graceful shutdown).

**Recommendation**: Yes. SQS visibility timeout (5 min) covers spot interruption grace period.

**Decision Required**: Confirm spot for workspace worker.

---

## OD-026: Cost Limit Default per Workspace

**Status**: Not specified; need default for `cost_limit_usd`.

**Context**: 
- Prevents runaway spend per workspace.
- User can override at provision time.
- Tracks via `accumulated_cost_usd` + CloudWatch billing metrics.

**Options**:
1. **$5.00** — ~4 hrs on t3.medium; conservative.
2. **$10.00** — ~8 hrs; more flexible.
3. **$20.00** — Full day on t3.large.

**Impact**: Workstream I (WorkspaceService), C (Workspaces API), F (CloudWatch billing alarm).

**Recommendation**: $5.00 default, user-overridable up to $50.

**Decision Required**: Confirm default cost limit.

---

## OD-027: Recovery Branch Naming Convention

**Status**: Not specified; need convention for Git handoff branches.

**Context**: 
- On workspace terminate, uncommitted changes pushed to branch.
- Branch name must be unique, sortable, identifiable.
- User needs to find and merge/PR the branch.

**Options**:
1. **`workspace/<instance_id>/<ISO_timestamp>`** — e.g., `workspace/01JABC123/2026-10-18T14:30:00Z`
2. **`workspace/<instance_id>/<short_id>`** — e.g., `workspace/01JABC123/abc123`
3. **`recovery/<user>/<instance_id>/<timestamp>`** — Namespaced by user.

**Impact**: Workstream I (Git handoff), C (Workspace terminate response).

**Recommendation**: Option 1. Sortable, unique, self-documenting.

**Decision Required**: Confirm naming convention.

---

## OD-028: Companion Device Auth — Cognito Device Tracking vs Custom

**Status**: ARCHITECTURE_DECISIONS.md says Cognito device tracking enabled.

**Context**: 
- Companion needs device identity for registration + JWT issuance.
- Cognito device tracking: built-in, managed, supports MFA.
- Custom: More control, more code.

**Options**:
1. **Cognito device tracking (locked)** — Enable in user pool; device JWT includes `device_id`.
2. **Custom device registry** — Our `devices` table issues JWTs; Cognito only for user auth.

**Impact**: Workstream C (Auth), H (Companion auth), F (Cognito config).

**Recommendation**: Cognito device tracking. Leverages managed feature; integrates with hosted UI.

**Decision Required**: Confirm Cognito device tracking.

---

## OD-029: Offline Queue Storage (Resolved)

**Decision**: Browser offline operations use IndexedDB. The local companion has
no local queue and sends operations through the authenticated cloud protocol
when connected. Server receipts, revisions, conflicts, tombstones, and feed
events are PostgreSQL-backed.

If durable disconnected companion edits become a requirement, evaluate a
local PostgreSQL append-only outbox as a separate decision. It must retain
operations until a cloud acknowledgement is durably recorded, while cloud
PostgreSQL remains canonical. SQLite is not an allowed fallback.

---

## OD-030: Conflict Preservation for Markdown Documents

**Status**: ARCHITECTURE_DECISIONS.md says "per-document LWW with conflict preservation."

**Context**: 
- Project documents (Markdown) can be edited both locally and in cloud.
- LWW may lose meaningful edits (e.g., two people edit different sections).
- Need to preserve both versions for manual merge.

**Options**:
1. **Store both versions** — On conflict, create `conflict_record` with `server_version` + `client_version`. UI shows diff/merge.
2. **LWW only** — Simpler, but risks data loss.
3. **Operational Transform** — Complex; overkill for Markdown.

**Impact**: Workstream A (conflict function), C (Sync routes), E (Conflict UI).

**Recommendation**: Option 1. Store both; UI shows 3-way merge (base/server/client).

**Decision Required**: Confirm conflict preservation approach.

---

## Decision Tracking

| ID | Decision | Status | Owner | Deadline |
|----|----------|--------|-------|----------|
| OD-001 | Compute: EC2 | Pending | User | Week 1 |
| OD-002 | RDS sizing | Pending | User | Week 1 |
| OD-003 | SQS FIFO | Pending | User | Week 1 |
| OD-004 | Cognito Hosted UI | Pending | User | Week 1 |
| OD-005 | Export zip | Pending | User | Week 3 |
| OD-006 | Conflict LWW | Pending | User | Week 2 |
| OD-007 | Offline scope | Pending | User | Week 2 |
| OD-008 | TUI/CLI strategy | Pending | User | Week 1 |
| OD-009 | Real LLM CI | Pending | User | Week 4 |
| OD-010 | Migration policy | Pending | User | Week 2 |
| OD-011 | Search backend | Pending | User | Week 3 |
| OD-012 | Timezone handling | Pending | User | Week 2 |
| OD-013 | Voice capture v1 | Pending | User | Week 3 |
| OD-014 | S3 CORS domain | Pending | User | Week 3 |
| OD-015 | Alarm thresholds | Pending | User | Week 5 |
| OD-016 | Frontend data mapping | Pending | User | Week 2 |
| OD-017 | Companion RPC capabilities | Pending | User | Week 3 |
| OD-018 | Session token TTL | Pending | User | Week 2 |
| OD-019 | mTLS vs JWT | Pending | User | Week 2 |
| OD-020 | Workspace template format | Pending | User | Week 3 |
| OD-021 | GitHub auth | Pending | User | Week 3 |
| OD-022 | Max concurrent workspaces | Pending | User | Week 3 |
| OD-023 | Idle timeout | Pending | User | Week 3 |
| OD-024 | Max runtime | Pending | User | Week 3 |
| OD-025 | Spot instances | Pending | User | Week 3 |
| OD-026 | Cost limit | Pending | User | Week 3 |
| OD-027 | Recovery branch naming | Pending | User | Week 3 |
| OD-028 | Device auth | Pending | User | Week 2 |
| OD-029 | Offline queue storage | Resolved: browser IndexedDB; no companion queue | M3 storage correction |
| OD-030 | Markdown conflict preservation | Pending | User | Week 2 |

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
