# Compatibility Findings — Repository vs. Locked Architecture

**Source**: AUDIT_REPORT.md (forensic audit of existing Personal OS backend) + Hybrid Architecture Addendum
**Scope**: Only findings requiring changes for the locked architecture. Non-blocking observations excluded.

---

## Finding CF-001: No API Layer Exists

| Aspect | Detail |
|--------|--------|
| **Existing** | No FastAPI, no HTTP handlers, no REST endpoints. CLI (`cli/main.py`) and TUI (`tui/app.py`) call `get_vault()`/`get_config()` singletons directly. |
| **Required** | FastAPI application with 14 route groups (API_CONTRACT.md §3), thin handlers delegating to service layer. |
| **Blocking?** | Yes — Workstream C cannot start until Workstream A provides repository interfaces. |
| **Workstream** | C (API & Authentication) |
| **Locked Decision Change?** | No — architecture explicitly requires API layer. |

---

## CF-002: `VaultManager` is Filesystem+Git, Not SQL

| Aspect | Detail |
|--------|--------|
| **Existing** | `core/vault.py`: `VaultManager` writes Markdown files to vault directory, commits to local Git. All domain data (tasks, projects, captures, progress) lives in `.md` files. |
| **Required** | Replace with SQLAlchemy repositories backed by PostgreSQL (RDS). Markdown becomes export/import format only (DATA_MODEL.md §9). |
| **Blocking?** | Yes — All persistence goes through `VaultManager` today. Workstream A must implement full replacement. |
| **Workstream** | A (Domain Schema & Persistence), B (Core Adaptation) |
| **Locked Decision Change?** | No — architecture specifies PostgreSQL as canonical operational database. |

---

## CF-003: `get_provider()` Ignores Configured Model Provider

| Aspect | Detail |
|--------|--------|
| **Existing** | `core/llm.py:62`: `getattr(config, "provider", "openrouter")` — but `Config` dataclass has no `.provider` attribute. Provider config lives at `config.model.provider`. Always defaults to `"openrouter"`. |
| **Required** | Fix to accept `ModelConfig` and read `provider`, `model_name`, `base_url` from it. |
| **Blocking?** | Yes — If `OPENROUTER_API_KEY` is set, user cannot switch to local/OpenAI/Anthropic provider. |
| **Workstream** | B (Core Adaptation) — Task B1 |
| **Locked Decision Change?** | No — Fix aligns with existing config structure. |

---

## CF-004: Project Aliases Not Passed to Semantic Pipeline

| Aspect | Detail |
|--------|--------|
| **Existing** | `processor.py:95-105` defines `project_aliases` for regex `KnowledgeExtractor`. `resolver.py:222-232` defines `DEFAULT_ALIASES`. But `_process_semantic()` creates `ProjectResolver()` **without aliases** (resolver.py:130). LLM path cannot resolve "maestro" → "Maestro". |
| **Required** | `SemanticExtractor` must accept and forward aliases to `ProjectResolver`. Aliases should come from config (single source). |
| **Blocking?** | Yes — Semantic extraction produces wrong project assignments without aliases. |
| **Workstream** | B (Core Adaptation) — Task B1 |
| **Locked Decision Change?** | No — Fix uses existing alias constants. |

---

## CF-005: `delete_file` Broken `commit=False` Branch

| Aspect | Detail |
|--------|--------|
| **Existing** | `vault.py:238-253`: `else` branch (when `commit=False`) still runs `index.remove()` + `commit()`. Also has indentation bug (12 spaces vs 8). |
| **Required** | N/A — `VaultManager` is replaced by SQLAlchemy repositories. Soft delete via `deleted_at` column replaces this. |
| **Blocking?** | No — Code path eliminated by architecture. |
| **Workstream** | A (replaces vault) |
| **Locked Decision Change?** | No — Architecture decision replaces vault entirely. |

---

## CF-006: `weekly_review.generate()` Bypasses `VaultManager`

| Aspect | Detail |
|--------|--------|
| **Existing** | `weekly_review.py:388`: `output_file.write_text()` — no atomic write, no git commit. |
| **Required** | N/A — Weekly reviews become derived data (StatsService) or exported Markdown (ExportService), not persisted as operational records. |
| **Blocking?** | No — Replaced by architecture. |
| **Workstream** | C (Stats + Export routes) |
| **Locked Decision Change?** | No. |

---

## CF-007: `rollback_batch` Can Delete Pre-existing Files

| Aspect | Detail |
|--------|--------|
| **Existing** | `vault.py:190-209`: `rollback_batch()` unlinks ALL files in `_batch_files`, including pre-existing files modified during batch. |
| **Required** | N/A — Batch git commits replaced by DB transactions. Rollback = transaction rollback. |
| **Blocking?** | No — Eliminated by architecture. |
| **Workstream** | A (DB transactions replace batch logic) |
| **Locked Decision Change?** | No. |

---

## CF-008: Duplicate Alias Definitions

| Aspect | Detail |
|--------|--------|
| **Existing** | `processor.py:95-105` and `resolver.py:222-232` both define identical alias maps. Risk of divergence. |
| **Required** | Single source of truth in config (e.g., `ModelConfig` or new `ExtractionConfig`). Injected into both `KnowledgeExtractor` and `ProjectResolver`. |
| **Blocking?** | Low — Works today but maintenance risk. Fix in B1. |
| **Workstream** | B (Core Adaptation) |
| **Locked Decision Change?** | No. |

---

## CF-009: No Idempotency Keys in Processing Pipeline

| Aspect | Detail |
|--------|--------|
| **Existing** | `BackgroundProcessor` uses in-memory `asyncio.Queue`. No persistence, no deduplication. If TUI restarts, queued captures lost. |
| **Required** | `processing_jobs` table with `idempotency_key` (ULID). Worker deduplicates on `idempotency_key`. SQS FIFO content-based deduplication. |
| **Blocking?** | Yes — Required for reliable async processing (ARCHITECTURE_DECISIONS.md §4). |
| **Workstream** | A (Schema), D (Worker) |
| **Locked Decision Change?** | No — Architecture mandates idempotent processing. |

---

## CF-010: No Authentication / Authorization

| Aspect | Detail |
|--------|--------|
| **Existing** | No auth. CLI/TUI run as local user with full vault access. |
| **Required** | Cognito JWT validation on all API routes. User-scoped data access. **Plus device credentials for companion daemon.** |
| **Blocking?** | Yes — Required for deployed multi-user environment. |
| **Workstream** | C (API & Authentication) |
| **Locked Decision Change?** | No — Architecture specifies Cognito. |

---

## CF-011: No Async Queue Infrastructure

| Aspect | Detail |
|--------|--------|
| **Existing** | `BackgroundProcessor` runs in-process asyncio task. No persistence, no horizontal scaling. |
| **Required** | SQS FIFO queue for capture processing. Worker process consumes messages. **Plus Standard queue for workspace provisioning.** |
| **Blocking?** | Yes — Required for deployed architecture (EC2 + worker). |
| **Workstream** | D (Async Processing), F (Infrastructure) |
| **Locked Decision Change?** | No. |

---

## CF-012: No File Upload / S3 Integration

| Aspect | Detail |
|--------|--------|
| **Existing** | No file handling. Voice capture writes local WAV then transcribes. |
| **Required** | S3 presigned URLs for upload/download. File metadata in `files` table. **Plus workspace template bucket.** |
| **Blocking?** | Yes — Required for Files domain (API_CONTRACT.md §3.7) and workspace templates. |
| **Workstream** | C (Files routes), F (S3 bucket + CORS) |
| **Locked Decision Change?** | No. |

---

## CF-013: No Search API (Only Local ripgrep/Python)

| Aspect | Detail |
|--------|--------|
| **Existing** | `core/search.py`: `SearchEngine` uses ripgrep or Python fallback on local filesystem. |
| **Required** | PostgreSQL FTS (tsvector) + optional Tantivy. SearchService with API endpoint. |
| **Blocking?** | Yes — Required for Search domain. |
| **Workstream** | A (FTS indexes), C (Search route) |
| **Locked Decision Change?** | No. |

---

## CF-014: No Sync / Conflict Resolution

| Aspect | Detail |
|--------|--------|
| **Existing** | Single-user local vault. No conflict detection, no offline queue, no revision tracking. |
| **Required** | `sync_metadata` table, reconciliation logic, conflict API endpoints (API_CONTRACT.md §4). **Extended for: tasks, projects, captures, focus sessions, project documents, device operations.** |
| **Blocking?** | Yes — Required for multi-device sync (architecture §4). |
| **Workstream** | A (Schema + conflict function), C (Sync routes), E (Frontend offline queue) |
| **Locked Decision Change?** | No — Architecture specifies LWW with safeguards. |

---

## CF-015: `KnowledgeApplier` Has Duplicate Typed + Dict Paths

| Aspect | Detail |
|--------|--------|
| **Existing** | `processor.py:162-208` (`apply_action` typed) and `processor.py:411-563` (`apply` legacy dict) implement nearly identical vault-write logic. |
| **Required** | Consolidate: `apply(dict)` converts to `VaultAction` and delegates to `apply_action()`. Or remove legacy path entirely (only semantic pipeline used in v1). |
| **Blocking?** | Low — Works but maintenance burden. Can defer to post-v1. |
| **Workstream** | B (Core Adaptation) — Optional |
| **Locked Decision Change?** | No. |

---

## CF-016: Frontend Uses LocalStorage, No API Client

| Aspect | Detail |
|--------|--------|
| **Existing** | `ui/src/lib/blockday-store.tsx` persists all state to `localStorage` (key `blockday-v2`). Mock data in `blockday-data.ts`. No network calls. |
| **Required** | Replace store with API-backed implementation. Keep localStorage only for offline queue + ephemeral UI state. **Add IndexedDB offline queue with reconciliation.** |
| **Blocking?** | Yes — Frontend cannot connect to backend without this. |
| **Workstream** | E (Frontend API Integration) |
| **Locked Decision Change?** | No. |

---

## CF-017: Frontend Data Shapes Differ from Backend Domain

| Aspect | Detail |
|--------|--------|
| **Existing** | `blockday-data.ts` defines `Task`, `Project`, `DiaryEntry` with fields like `dueIn` (days from viewed day), `start` (minutes from 06:00), `mood`, `frog`. No `revision`, no `user_id`, no `idempotency_key`. |
| **Required** | API schemas (API_CONTRACT.md) define canonical shapes. Frontend adapts: `dueIn` computed from `deadline` + viewed date; `start_minute` ↔ `start`; `revision` sent in `If-Match`. |
| **Blocking?** | Yes — Direct mapping will fail. Adapter layer needed. |
| **Workstream** | E (Frontend API Integration) |
| **Locked Decision Change?** | No — Architecture says "Do not duplicate domain behavior in TypeScript." Frontend adapts to API. |

---

## CF-018: No Database Test Infrastructure

| Aspect | Detail |
|--------|--------|
| **Existing** | Tests use temp vault directories (`tests/conftest.py`). No PostgreSQL, no SQLAlchemy, no migration testing. |
| **Required** | Test fixtures for async SQLAlchemy session, Alembic migration tests on PG + SQLite, repository contract tests. |
| **Blocking?** | Yes — Workstream A needs test infrastructure before writing models. |
| **Workstream** | A (Domain Schema & Persistence) |
| **Locked Decision Change?** | No. |

---

## CF-019: `core/config.py` Missing DB/S3/Cognito Settings

| Aspect | Detail |
|--------|--------|
| **Existing** | `Config` has `vault`, `model`, `voice`, `search`, `ui`, `keys`. No database URL, S3 bucket, Cognito config. |
| **Required** | Add `DatabaseConfig`, `S3Config`, `CognitoConfig`, `CompanionConfig`, `WorkspaceConfig` nested dataclasses. Load from env/secrets. |
| **Blocking?** | Yes — All workstreams need config. |
| **Workstream** | A (Schema), B (Core Adaptation), C (API), F (Infrastructure) |
| **Locked Decision Change?** | No — Additive change. |

---

## CF-020: Extraction Pipeline Tests Only Cover Regex Fallback

| Aspect | Detail |
|--------|--------|
| **Existing** | `test_semantic_pipeline.py` mocks `LLMProvider` but tests assert on regex fallback behavior. `OPENROUTER_API_KEY` explicitly unset. Full LLM path untested. |
| **Required** | Contract tests for full pipeline with mocked LLM (already in test file — good). Integration tests with real OpenRouter in CI (optional, gated). |
| **Blocking?** | Low — Tests exist for mocked path. Real LLM test is quality gate, not blocker. |
| **Workstream** | B (Core Adaptation), G (Integration) |
| **Locked Decision Change?** | No. |

---

## CF-021: No Local Companion Daemon

| Aspect | Detail |
|--------|--------|
| **Existing** | No local daemon. Voice capture skill (`skills/voice_capture.py`) runs as one-off subprocess. |
| **Required** | New `companion/` package: daemon with device registration, heartbeat, WebSocket to session broker, approved RPC interface (terminal, filesystem, process, agent), project discovery. Companion sync uses authenticated cloud connectivity; no local queue. |
| **Blocking?** | Yes — Required for Milestone 2 (Local Integration) and hybrid architecture. |
| **Workstream** | H (Local Companion Protocol) |
| **Locked Decision Change?** | No — New component required by hybrid architecture. |

---

## CF-022: No Session Broker for Remote Access

| Aspect | Detail |
|--------|--------|
| **Existing** | No session broker, no WebSocket infrastructure, no remote terminal capability. |
| **Required** | WebSocket session broker on EC2 (port 8001): accepts companion connections, authenticates via JWT, multiplexes JSON-RPC 2.0 calls to companion, enforces capability-based authorization, logs to `session_audit`. |
| **Blocking?** | Yes — Required for Milestone 4 (Remote Laptop Access). |
| **Workstream** | H (Local Companion Protocol), F (Infrastructure — WebSocket endpoint) |
| **Locked Decision Change?** | No — Required by hybrid architecture. |

---

## CF-023: No Device Registry or Presence Tracking

| Aspect | Detail |
|--------|--------|
| **Existing** | No device concept. Single-user local vault. |
| **Required** | `devices` table: device_id, user_id, platform, capabilities, status (online/offline/stale), last_seen_at, public_key. Cognito device tracking enabled for device JWT issuance. Companion registers on startup, heartbeats every 30s. |
| **Blocking?** | Yes — Required for companion auth, capability gating, presence UI. |
| **Workstream** | A (Schema), C (Device routes), H (Companion registration) |
| **Locked Decision Change?** | No. |

---

## CF-024: No Cloud Workspace Provisioning

| Aspect | Detail |
|--------|--------|
| **Existing** | No cloud workspace concept. All work local. |
| **Required** | `workspace_templates` + `workspace_instances` tables. SQS queue → worker → EC2 RunInstances → UserData applies devcontainer/Nix/script → Git clone → tools start → WebSocket terminal via session broker. Git handoff on terminate. Cost limits + idle shutdown. |
| **Blocking?** | Yes — Required for Milestone 5 (On-Demand Cloud Development). |
| **Workstream** | I (Cloud Workspace Provisioning), D (Workspace Worker), F (IAM roles, EC2 permissions) |
| **Locked Decision Change?** | No — Required by hybrid architecture. |

---

## CF-025: No Workspace Template System

| Aspect | Detail |
|--------|--------|
| **Existing** | No workspace templates. |
| **Required** | S3 bucket `pos-deku-templates-{env}` with per-user prefixes. Templates: `devcontainer.json` (primary), `flake.nix`, `setup.sh`. Worker detects and applies. Per-project selection at provision time. |
| **Blocking?** | Yes — Required for reproducible workspace environments. |
| **Workstream** | I (Cloud Workspace Provisioning), F (S3 template bucket) |
| **Locked Decision Change?** | No. |

---

## CF-026: No Git-Based Handoff for Cloud Workspaces

| Aspect | Detail |
|--------|--------|
| **Existing** | No cloud workspace → no handoff needed. |
| **Required** | On workspace terminate: check Git status → push uncommitted changes to `workspace/<instance_id>/<timestamp>` branch → notify user with branch name. No silent modification of laptop working copy. |
| **Blocking?** | Yes — Required to prevent data loss and maintain source-of-truth boundaries. |
| **Workstream** | I (Cloud Workspace Provisioning) |
| **Locked Decision Change?** | No. |

---

## CF-027: No Session Audit Logging

| Aspect | Detail |
|--------|--------|
| **Existing** | No session concept, no audit logging. |
| **Required** | `sessions` table + `session_audit` table. Every RPC call logged: session_id, user_id, device_id, action, capability, rpc_method, redacted params, result, client_ip. Immutable append-only. |
| **Blocking?** | Yes — Required for security compliance and remote access governance. |
| **Workstream** | A (Schema), H (Companion RPC), C (Session routes) |
| **Locked Decision Change?** | No. |

---

## CF-028: No Capability-Based RPC Authorization

| Aspect | Detail |
|--------|--------|
| **Existing** | No RPC, no capability model. |
| **Required** | Capability system: `terminal.read`, `terminal.write`, `terminal.control`, `filesystem.read`, `filesystem.write`, `process.list`, `process.exec`, `agent.orchestrate`. Each companion session grants subset. Path allowlist for filesystem; command allowlist for process exec. Explicit approval for `terminal.control` and `process.exec`. |
| **Blocking?** | Yes — Required for secure remote access. |
| **Workstream** | H (Companion RPC), C (Companion session routes) |
| **Locked Decision Change?** | No. |

---

## CF-029: No Cost Tracking for Cloud Workspaces

| Aspect | Detail |
|--------|--------|
| **Existing** | No cloud workspaces, no cost tracking. |
| **Required** | `workspace_instances` tracks `cost_limit_usd`, `accumulated_cost_usd`. CloudWatch metric → alarm → auto-terminate on breach. Per-user concurrent workspace quota (default 2). |
| **Blocking?** | Yes — Required for cost control in Milestone 5. |
| **Workstream** | I (Cloud Workspace Provisioning), F (CloudWatch alarms) |
| **Locked Decision Change?** | No. |

---

## CF-030: No Idle/Max Runtime Enforcement for Workspaces

| Aspect | Detail |
|--------|--------|
| **Existing** | N/A |
| **Required** | Configurable `idle_timeout_minutes` (default 30) and `max_runtime_minutes` (default 240). Worker monitors and terminates. CloudWatch alarm as backup. |
| **Blocking?** | Yes — Required for cost control and resource hygiene. |
| **Workstream** | I (Cloud Workspace Provisioning), D (Workspace Worker) |
| **Locked Decision Change?** | No. |

---

## CF-031: No Concurrent Workspace Quota

| Aspect | Detail |
|--------|--------|
| **Existing** | N/A |
| **Required** | Per-user quota (default 2 concurrent workspaces). Enforced at provisioning API. |
| **Blocking?** | Yes — Required for resource management. |
| **Workstream** | I (Cloud Workspace Provisioning), C (Workspaces API) |
| **Locked Decision Change?** | No. |

---

## CF-032: No Recovery Branch Naming Convention

| Aspect | Detail |
|--------|--------|
| **Existing** | N/A |
| **Required** | On workspace terminate: push to `workspace/<instance_id>/<ISO_timestamp>` branch. User notified with branch name for manual recovery. |
| **Blocking?** | Yes — Required for data recovery UX. |
| **Workstream** | I (Cloud Workspace Provisioning) |
| **Locked Decision Change?** | No. |

---

## CF-033: Sync Requirements Extended Beyond Operational Records

| Aspect | Detail |
|--------|--------|
| **Existing** | CF-014 covers operational records only. |
| **Required** | Sync/reconciliation must also handle: project documents (Markdown, per-document conflict preservation), device registry (server TTL wins), and workspace state (server wins, ephemeral). Browser offline operations use IndexedDB. |
| **Blocking?** | Yes — Extended sync scope for hybrid architecture. |
| **Workstream** | A (conflict function per data type), C (Sync routes), E (Frontend offline queue) |
| **Locked Decision Change?** | No — Architecture addendum extends sync scope. |

---

## CF-034: No Device Credentials in Cognito

| Aspect | Detail |
|--------|--------|
| **Existing** | Cognito user pools only; no device tracking. |
| **Required** | Enable Cognito device tracking. Issue device JWTs with `device_id`, `user_id`, `capabilities` claims. Used for companion WebSocket auth. Pre-auth Lambda validates device status. |
| **Blocking?** | Yes — Required for companion authentication. |
| **Workstream** | C (Auth), F (Cognito config) |
| **Locked Decision Change?** | No. |

---

## CF-035: Companion Offline Queue Requirement Superseded

| Aspect | Detail |
|--------|--------|
| **Existing** | Voice capture runs online; no offline persistence. |
| **Decision** | No local companion database or queue. The companion submits operations using the authenticated cloud protocol when connected. A future durable offline requirement needs an approved PostgreSQL-compatible edge outbox. |
| **Blocking?** | No for M2 device connectivity and RPC; disconnected durable edits are not supported. |
| **Workstream** | M3 API/protocol integration (later) |
| **Locked Decision Change?** | Superseded by the M3 storage correction. |

---

## Summary Matrix (Updated)

| Finding | Blocks? | Workstream | Effort |
|---------|---------|------------|--------|
| CF-001: No API layer | Yes | C | High |
| CF-002: VaultManager is filesystem | Yes | A, B | High |
| CF-003: get_provider() broken | Yes | B | Low |
| CF-004: Aliases not passed to LLM path | Yes | B | Low |
| CF-005: delete_file bug | No (replaced) | A | N/A |
| CF-006: weekly_review bypass | No (replaced) | C | N/A |
| CF-007: rollback_batch destructive | No (replaced) | A | N/A |
| CF-008: Duplicate aliases | Low | B | Low |
| CF-009: No idempotency keys | Yes | A, D | Medium |
| CF-010: No auth | Yes | C | High |
| CF-011: No async queue | Yes | D, F | Medium |
| CF-012: No S3/files | Yes | C, F | Medium |
| CF-013: No search API | Yes | A, C | Medium |
| CF-014: No sync/conflict | Yes | A, C, E | High |
| CF-015: KnowledgeApplier duplicate paths | Low | B (optional) | Low |
| CF-016: Frontend localStorage only | Yes | E | High |
| CF-017: Frontend data shape mismatch | Yes | E | Medium |
| CF-018: No DB test infra | Yes | A | Medium |
| CF-019: Config missing DB/S3/Cognito | Yes | A, B, C, F | Low |
| CF-020: LLM path untested | Low | B, G | Low |
| **CF-021: No local companion daemon** | **Yes** | **H** | **High** |
| **CF-022: No session broker** | **Yes** | **H, F** | **High** |
| **CF-023: No device registry** | **Yes** | **A, C, H** | **Medium** |
| **CF-024: No cloud workspace provisioning** | **Yes** | **I, D, F** | **High** |
| **CF-025: No workspace templates** | **Yes** | **I, F** | **Medium** |
| **CF-026: No Git handoff** | **Yes** | **I** | **Medium** |
| **CF-027: No session audit** | **Yes** | **A, H, C** | **Medium** |
| **CF-028: No capability RPC auth** | **Yes** | **H, C** | **High** |
| **CF-029: No cost tracking** | **Yes** | **I, F** | **Medium** |
| **CF-030: No idle/max runtime enforcement** | **Yes** | **I, D** | **Medium** |
| **CF-031: No workspace quota** | **Yes** | **I, C** | **Low** |
| **CF-032: No recovery branch naming** | **Yes** | **I** | **Low** |
| **CF-033: Extended sync scope** | **Yes** | **A, C, E** | **High** |
| **CF-034: No device credentials in Cognito** | **Yes** | **C, F** | **Low** |
| **CF-035: No durable disconnected companion queue** | **No for M2 connectivity; yes if offline edits are required** | **Later M3 integration** | **Deferred** |

**Total Blocking Findings**: 25 (CF-001-004, 009-014, 016-019, 021-035)
**Total Non-Blocking**: 10 (CF-005-008, 015, 020)

---

## Adaptation Notes (No Architecture Changes Required)

1. **Vault → SQL**: Direct replacement. Markdown export/import preserves compatibility.
2. **Git batch → DB transactions**: Semantic equivalence. Rollback becomes transaction rollback.
3. **In-memory queue → SQS FIFO**: Same message format, adds persistence + deduplication.
4. **Local auth → Cognito JWT**: Standard pattern. No domain logic changes.
5. **localStorage → API + offline queue**: Frontend architecture adapts; backend unchanged.
6. **New companion package**: Separate `companion/` directory; builds to independent wheel; installs via `pipx` or system package manager.
7. **Session broker on EC2**: Co-located with API; WebSocket on separate port (8001); same ASG.
8. **Workspace provisioning via SQS + worker**: Same pattern as capture processing; Standard queue for lower throughput.
9. **Git for source sync**: Proven, handles merges, no FS replication needed.
10. **Devcontainer/Nix for templates**: Reproducible, project-specific, no baked secrets.
