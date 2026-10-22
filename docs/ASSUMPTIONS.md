# Architectural Assumptions — Deku / Pos-Deku

**Assumptions that underpin the architecture but cannot be fully validated until implementation.**  
Each assumption has a validation method and a fallback if invalid.

---

## A-001: Existing Extraction Pipeline Is Reusable As-Is

**Assumption**: The `core/extraction/` pipeline (models, validator, resolver, planner, actions, semantic_extractor) works correctly and only needs adaptation to the new persistence layer (SQLAlchemy instead of VaultManager).

**Validation**: 
- Run `test_semantic_pipeline.py` with mocked LLM — all 19 tests pass
- Run `test_extraction_resolver.py` — all 16 tests pass
- Run `test_extraction_validator.py` — all 12 tests pass
- Manual verification: `KnowledgeApplier.apply_action()` produces correct Markdown mutations

**Fallback if invalid**: 
- If pipeline has hidden dependencies on `VaultManager` filesystem behavior, create adapter layer in `services/processing.py` that translates `VaultAction` → SQLAlchemy operations
- If LLM prompts need adjustment for new data shapes, iterate on `semantic_extractor.py` prompts

**Risk**: Medium — pipeline is well-tested but untested against SQL persistence

---

## A-002: Capture Contract Maps Cleanly to DB + SQS

**Assumption**: The persist-first contract (`capture.py:persist_capture()` → `capture()` → `capture_and_process()`) maps directly to: INSERT into `captures` table → SQS message with `idempotency_key` → worker processes → updates `captures.processing_status` + writes `extracted_items` + mutates `tasks`/`projects`.

**Validation**:
- Unit test: `persist_capture()` returns `CaptureReceipt` with DB `capture_id`
- Integration test: Capture → SQS → worker → DB state matches VaultManager behavior
- Verify: `processing_status` transitions (pending → queued → running → succeeded/failed)

**Fallback if invalid**:
- If SQS introduces latency issues, add local in-memory queue as buffer with periodic flush
- If transaction boundaries differ, use explicit DB transaction in `ProcessingService`

**Risk**: Low — pattern is well-understood; VaultManager already had batch commit logic

---

## A-003: SQLAlchemy + Alembic Works for Schema Evolution

**Assumption**: SQLAlchemy 2.0 + Alembic can express the full schema (15 tables, triggers, FTS, JSONB) and migrations run cleanly on both PostgreSQL 16 and SQLite (with json1).

**Validation**:
- `alembic upgrade head` on fresh PostgreSQL 16
- `alembic upgrade head` on SQLite with `json1` pragma
- `alembic downgrade base` → `upgrade head` round-trip
- CI runs both dialects

**Fallback if invalid**:
- If SQLite FTS5 unavailable, use Tantivy for SQLite search, PG FTS for production
- If trigger syntax differs, use raw SQL in migration for PostgreSQL, skip for SQLite
- If JSONB indexing issues, use GIN on specific keys only

**Risk**: Medium — SQLite compatibility often requires dialect-specific handling

---

## A-004: Cognito Device Tracking Works for Companion Auth

**Assumption**: Cognito device tracking can issue device-scoped JWTs with `device_id` claim, usable for companion WebSocket authentication and capability gating.

**Validation**:
- Enable device tracking in user pool
- Register test device via `POST /devices`
- Verify device JWT contains `device_id` and `capabilities` claims
- Test WebSocket connection with device JWT to session broker
- Test pre-auth Lambda validates device status (revoked/stale)

**Fallback if invalid**:
- If Cognito device tracking insufficient, implement custom device JWT issuance in `services/devices.py` using `pos-deku/companion-jwt-{env}` secret
- Store device credentials in `devices` table with `public_key` for mTLS option

**Risk**: Medium — Cognito device tracking is less documented than user auth

---

## A-005: Outbound-Only WebSocket Works for Companion

**Assumption**: Companion daemon can maintain persistent outbound WebSocket to session broker (port 8001) through NAT/firewall, with automatic reconnection and heartbeat.

**Validation**:
- Run companion daemon behind typical home/office NAT
- Simulate network interruption (kill -STOP, network disconnect)
- Verify automatic reconnection with exponential backoff
- Verify heartbeat (30s) detects stale connections on broker
- Test multiple companions behind same NAT

**Fallback if invalid**:
- If outbound WebSocket blocked, add WebSocket over HTTP/2 upgrade via ALB
- If persistent connection unreliable, add polling fallback for command delivery
- Consider Tailscale/WireGuard for guaranteed connectivity (post-v1)

**Risk**: Medium — NAT traversal varies by environment

---

## A-006: SQS FIFO Provides Exactly-Once for Capture Processing

**Assumption**: SQS FIFO with content-based deduplication (`idempotency_key` as deduplication ID) + `MessageGroupId=user_id` provides exactly-once processing semantics for capture pipeline.

**Validation**:
- Send duplicate messages with same `idempotency_key` → only one processed
- Verify ordering per `MessageGroupId` (user)
- Test worker crash mid-processing → message returns to queue → reprocessed exactly once
- Test DLQ capture after 3 retries

**Fallback if invalid**:
- If deduplication fails, add application-level dedup in `ProcessingService` using `idempotency_key` unique constraint on `processing_jobs`
- If ordering critical, add sequence numbers to messages

**Risk**: Low — SQS FIFO deduplication is well-documented

---

## A-007: PostgreSQL FTS Meets Search Quality Requirements

**Assumption**: PostgreSQL `tsvector` + GIN index on `tasks`, `captures`, `project_documents` provides sufficient search quality (ranking, highlighting, latency <100ms on 10k rows).

**Validation**:
- Load 10k test rows across tables
- Run benchmark queries: prefix, phrase, multi-term
- Measure p50/p95 latency
- Compare ranking quality vs Tantivy baseline

**Fallback if invalid**:
- If ranking insufficient, add Tantivy as async indexer (worker updates Tantivy index on write)
- If latency high, add materialized view for common queries
- If highlighting needed, use `ts_headline` function

**Risk**: Low — PG FTS is mature; 10k rows is small

---

## A-008: Cloud Workspace Provisioning Completes in <5 Minutes

**Assumption**: EC2 `RunInstances` + UserData (Git clone + devcontainer/Nix apply + tool startup) completes in <5 minutes for typical repos.

**Validation**:
- Provision test workspace with sample repo (100MB, 500 files)
- Measure: instance boot + UserData execution + tool readiness
- Test with devcontainer, Nix flake, and setup.sh templates
- Test cold start vs warm (AMI with pre-installed tools)

**Fallback if invalid**:
- If >5 min, pre-bake AMI with Docker, VS Code Server, common language runtimes
- If Git clone slow, use shallow clone + sparse checkout
- If Nix slow, use binary cache (Cachix) or pre-built devcontainer image

**Risk**: Medium — provisioning time varies significantly by repo size and template

---

## A-009: Git Handoff Preserves Uncommitted Work Reliably

**Assumption**: On workspace terminate, `git status` → push uncommitted changes to `workspace/<instance_id>/<timestamp>` branch reliably recovers user work without conflicts.

**Validation**:
- Create workspace, make uncommitted changes (new files, modified, deleted, staged/unstaged)
- Terminate workspace
- Verify branch created with all changes
- Verify user can `git fetch` and `git checkout` recovery branch
- Test edge cases: binary files, submodules, large files (>100MB)

**Fallback if invalid**:
- If Git push fails, fall back to S3 artifact upload (tar.gz of working directory)
- If conflicts on recovery, provide `git merge` guidance in notification
- For large files, use Git LFS (configure in template)

**Risk**: Medium — Git edge cases can lose work if not handled

---

## A-010: Cost Controls Prevent Runaway Spend

**Assumption**: Per-workspace cost limit ($5 default) + CloudWatch billing alarms + idle/max runtime enforcement prevent unexpected charges.

**Validation**:
- Provision workspace, leave running past idle timeout → auto-terminate
- Provision workspace, run CPU-intensive task past max runtime → auto-terminate
- Provision workspace, exceed cost limit → auto-terminate
- Verify CloudWatch alarm triggers on `accumulated_cost_usd` metric
- Test concurrent workspace quota enforcement (2 default)

**Fallback if invalid**:
- If billing metrics delayed, use EC2 pricing API + instance uptime for real-time estimation
- If alarm missed, add Lambda scheduled check every 5 min as backup
- Hard limit: IAM policy denies `RunInstances` if user quota exceeded

**Risk**: Low — multiple enforcement layers

---

## A-011: Markdown Export/Import Round-Trips Without Data Loss

**Assumption**: Export (DB → Markdown zip) → Import (Markdown zip → DB) preserves all task, capture, project document data without loss.

**Validation**:
- Create project with all 8 document kinds, tasks (done/open/archived), captures with extractions
- Export → Import into new project
- Verify: task count, completion status, deadlines, estimates, mood, frog
- Verify: capture raw text, source, timestamp, extractions
- Verify: all 8 document kinds content preserved
- Verify: project hierarchy (parent/child) preserved

**Fallback if invalid**:
- If frontmatter parsing fails, use more robust parser (pandoc)
- If semantic blocks lost, embed in HTML comments as before
- If project hierarchy lost, store parent_id in README frontmatter

**Risk**: Medium — Markdown round-trip is inherently lossy for some metadata

---

## A-011: Frontend Adapter Layer Handles Data Shape Differences

**Assumption**: Frontend adapter in `blockday-api.ts` can convert between backend canonical shapes (`deadline` date, `start_minute` int) and frontend UI shapes (`dueIn` days-from-viewed, `start` minutes) without bugs.

**Validation**:
- Test: Create task with deadline → API returns `deadline` date → frontend computes `dueIn` correctly for today/tomorrow/overdue
- Test: Frontend creates task with `dueIn` → adapter converts to `deadline` date → backend stores correctly
- Test: `start_minute` ↔ `start` conversion for Flow scheduling
- Test: Timezone edge cases (DST, cross-day, user profile timezone)

**Fallback if invalid**:
- If conversion bugs persistent, add computed fields to API response (`dueIn`, `start`)
- If frontend refactor needed, migrate to backend shapes incrementally

**Risk**: Low — straightforward date arithmetic

---

## A-012: Companion Offline Queue Survives Daemon Crashes (Superseded)

**Decision**: Rejected by the M3 storage correction. The companion has no local
operational database or durable offline queue. It submits sync operations via
the authenticated cloud protocol when connected. Durable edits made while
disconnected are outside the current companion behavior.

If the product later requires durable disconnected edits, evaluate a local
PostgreSQL append-only outbox that retains operations until the cloud
PostgreSQL acknowledgement has been durably recorded. This requires a separate
decision about local PostgreSQL installation, upgrades, and recovery support.
No SQLite fallback is permitted.

---

## A-013: Session Broker Scales to Target Concurrency

**Assumption**: Single session broker on EC2 (ASG max=3) handles target concurrency (1000 companion connections, 1000 msg/min/session) with acceptable latency.

**Validation**:
- Load test: 1000 simulated companions connect, send RPC at 10 msg/min
- Measure: connection establishment latency, RPC round-trip latency, memory/CPU
- Test: broker restart → companions reconnect gracefully
- Test: broker scale-out (ASG adds instance) → new connections route correctly

**Fallback if invalid**:
- If single broker bottlenecks, add Redis pub/sub for horizontal scaling (multiple broker instances)
- If WebSocket overhead high, consider gRPC over HTTP/2 for RPC
- Add connection pooling on broker side

**Risk**: Medium — WebSocket scaling can be tricky

---

## A-013: Real LLM Integration Works with Current Prompts

**Assumption**: OpenRouter (Gemma 4 31B) with current `semantic_extractor.py` prompts produces valid `RawExtraction` JSON that passes `ExtractionValidator`.

**Validation**:
- PR-gated test: `pytest --run-real-llm` on sample captures
- Measure: success rate, latency, token cost
- Test edge cases: empty capture, very long capture, ambiguous project refs

**Fallback if invalid**:
- If JSON parsing fails, add retry with stricter prompt
- If validation fails often, relax `ExtractionValidator` or improve prompts
- If cost high, add caching for repeated captures

**Risk**: Low — prompts are tested with mock; real LLM behavior similar

---

## A-014: Markdown Document Sync Conflict Resolution Is Usable

**Assumption**: Per-document conflict preservation (storing both server + client versions) with 3-way merge UI is sufficient for user to resolve conflicts without data loss.

**Validation**:
- Simulate: User edits `Notes.md` locally offline; another user edits same doc in cloud
- Reconcile → conflict record created with both versions
- UI shows 3-way diff (base/server/client)
- User merges → result saved
- Test: concurrent edits to different sections (should auto-merge)

**Fallback if invalid**:
- If 3-way merge too complex, fallback to "server wins" with client version saved as `.conflict.md` in project
- If UI too complex, use simple "choose one" dialog with preview

**Risk**: Medium — merge UX is hard to get right

---

## A-015: Terraform State Management Works for Team

**Assumption**: Terraform state in S3 + DynamoDB locking works for concurrent developers and CI/CD without conflicts.

**Validation**:
- Two developers run `terraform apply` concurrently → one waits for lock
- CI/CD pipeline applies to `dev` workspace
- State file versioning enables rollback
- `terraform plan` in PR shows changes

**Fallback if invalid**:
- If locking issues, use `terraform lock`/`force-unlock` procedures
- If state corruption, restore from S3 versioning
- Consider Terraform Cloud for managed state

**Risk**: Low — standard Terraform pattern

---

## Validation Checklist (Pre-Implementation)

| Assumption | Validation Method | Owner | Target |
|------------|-------------------|-------|--------|
| A-001 | Run extraction tests | Workstream B | Week 3 |
| A-002 | Capture→SQS→DB integration test | Workstream B/D | Week 5 |
| A-003 | Alembic PG+SQLite | Workstream A | Week 3 |
| A-004 | Cognito device JWT + WebSocket | Workstream C/H | Week 5 |
| A-005 | Companion WebSocket behind NAT | Workstream H | Week 8 |
| A-006 | SQS FIFO dedup + ordering | Workstream D | Week 7 |
| A-007 | PG FTS benchmark | Workstream A/C | Week 7 |
| A-008 | Workspace provisioning time | Workstream I | Week 13 |
| A-009 | Git handoff recovery | Workstream I | Week 13 |
| A-010 | Cost control enforcement | Workstream I/F | Week 13 |
| A-011 | Markdown round-trip | Workstream C/E | Week 9 |
| A-012 | Adapter layer conversion | Workstream E | Week 9 |
| A-013 | Companion offline queue durability | Superseded by M3 storage correction | Resolved |
| A-014 | Broker scaling | Workstream H/F | Week 11 |
| A-015 | Real LLM integration | Workstream B/G | Week 4 |

---

## Assumptions Not Requiring Pre-Validation (Deferred)

| Assumption | Why Deferred |
|------------|--------------|
| Frontend performance with 10k tasks | Can optimize later (virtualization, pagination) |
| Companion RPC latency over Internet | Measured at M4; optimize if needed |
| Workspace template ecosystem adoption | User-driven; start with devcontainer |
| Multi-user collaboration on same project | Post-v1; single-user first |
| Advanced agent orchestration via RPC | Post-v1; Hermes integration later |
