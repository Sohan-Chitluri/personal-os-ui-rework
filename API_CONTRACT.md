# API Contract — Deku / Pos-Deku Backend

**Cross-references**: [ARCHITECTURE_DECISIONS.md](ARCHITECTURE_DECISIONS.md), [DATA_MODEL.md](DATA_MODEL.md), [BACKEND_IMPLEMENTATION_PLAN.md](BACKEND_IMPLEMENTATION_PLAN.md)

---

## 1. Conventions

| Aspect | Standard |
|--------|----------|
| Base Path | `/api/v1` |
| Auth | Bearer token (Cognito JWT) |
| Content-Type | `application/json` |
| IDs | ULID (encoded as UUID in JSON) |
| Dates | ISO 8601 (`timestamptz` → `2026-10-18T14:30:00Z`) |
| Dates (date-only) | `YYYY-MM-DD` |
| Errors | RFC 7807 Problem Details |
| Pagination | Cursor-based (`after`, `limit`) |
| Optimistic Lock | `If-Match: <revision>` header on UPDATE/DELETE |
| Rate Limit | 100 req/min per user (configurable) |

### Error Response Format
```json
{
  "type": "https://api.pos-deku.com/errors/conflict",
  "title": "Resource conflict",
  "status": 409,
  "detail": "Task was modified by another request. Current revision: 5",
  "instance": "/api/v1/tasks/t_abc123",
  "extensions": {
    "current_revision": 5,
    "server_state": { "title": "Updated title", "revision": 5 }
  }
}
```

### Common Error Types
| Status | Type | When |
|--------|------|------|
| 400 | `validation_error` | Request body invalid |
| 401 | `unauthorized` | Missing/invalid token |
| 403 | `forbidden` | Token valid but insufficient scope |
| 404 | `not_found` | Resource doesn't exist |
| 409 | `conflict` | Revision mismatch, structural conflict |
| 422 | `unprocessable` | Semantic validation failed (e.g., circular project ref) |
| 429 | `rate_limited` | Too many requests |
| 500 | `internal` | Unexpected server error |

---

## 2. Authentication & Authorization

### Token Validation
- JWT from Cognito User Pool
- Verified: `exp`, `aud` (client_id), `iss` (user pool), signature
- Claims extracted: `sub` → `user_id`, `cognito:groups` → roles, `device_id` (for companion)

### Scopes (Cognito Groups)
| Group | Permissions |
|-------|-------------|
| `user` | Full CRUD on own data |
| `admin` | All user data + system endpoints |

### Device Credentials (Companion)
- Separate device JWT issued by Cognito (device tracking enabled)
- Contains: `device_id`, `user_id`, `capabilities` claim
- Used for companion WebSocket authentication

### Dependency Injection (FastAPI)
```python
# api/deps.py
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = credentials.credentials
    claims = verify_cognito_jwt(token)  # raises 401/403
    user = await user_repo.get_or_create(claims["sub"], claims.get("email"))
    return user

async def get_current_device(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
    db: AsyncSession = Depends(get_db),
) -> Device:
    token = credentials.credentials
    claims = verify_cognito_device_jwt(token)  # validates device_id claim
    device = await device_repo.get(claims["device_id"])
    if not device or device.revoked_at:
        raise HTTPException(401, "Device revoked")
    return device
```

---

## 3. Endpoint Groups

### 3.1 Tasks (`/api/v1/tasks`)

| Method | Path | Operation | Request | Response |
|--------|------|-----------|---------|----------|
| GET | `/` | List tasks | `?project_id=&done=&due_before=&after=&limit=` | `TaskList` |
| POST | `/` | Create task | `TaskCreate` | `Task` |
| GET | `/{task_id}` | Get task | — | `Task` |
| PATCH | `/{task_id}` | Update task | `TaskUpdate` + `If-Match` | `Task` |
| DELETE | `/{task_id}` | Soft delete task | `If-Match` | `204` |
| POST | `/{task_id}/complete` | Toggle complete | `If-Match` | `Task` |
| POST | `/{task_id}/archive` | Move to archive | `If-Match` | `Task` |
| GET | `/{task_id}/history` | Completion history | — | `TaskHistoryList` |

**Schemas**:
```python
class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    project_id: UUID
    estimate: int = Field(default=15, ge=1, le=480)
    mood: Literal["Deep", "Clear", "Creative", "Easy"] = "Clear"
    due_in: Optional[int] = Field(default=None, ge=-365, le=365)  # days from today
    frog: bool = False
    start_minute: Optional[int] = Field(default=None, ge=0, le=1440)

class TaskUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    estimate: Optional[int] = Field(default=None, ge=1, le=480)
    mood: Optional[Literal["Deep", "Clear", "Creative", "Easy"]] = None
    due_in: Optional[int] = Field(default=None, ge=-365, le=365)
    frog: Optional[bool] = None
    project_id: Optional[UUID] = None  # move between projects

class Task(BaseModel):
    id: UUID
    project_id: UUID
    title: str
    estimate: int
    mood: Literal["Deep", "Clear", "Creative", "Easy"]
    frog: bool
    due_in: Optional[int]
    start_minute: Optional[int]
    done: bool
    completed_at: Optional[datetime]
    archived_at: Optional[datetime]
    revision: int
    created_at: datetime
    updated_at: datetime

class TaskList(BaseModel):
    items: List[Task]
    next_cursor: Optional[str]  # opaque cursor for pagination
```

---

### 3.2 Projects (`/api/v1/projects`)

| Method | Path | Operation | Request | Response |
|--------|------|-----------|---------|----------|
| GET | `/` | List root projects | `?include_children=` | `ProjectList` |
| POST | `/` | Create project | `ProjectCreate` | `Project` |
| GET | `/{project_id}` | Get project | — | `ProjectDetail` |
| PATCH | `/{project_id}` | Update project | `ProjectUpdate` + `If-Match` | `Project` |
| DELETE | `/{project_id}` | Soft delete | `If-Match` | `204` |
| GET | `/{project_id}/children` | List child projects | — | `ProjectList` |
| GET | `/{project_id}/documents` | Get all documents | — | `ProjectDocuments` |
| PATCH | `/{project_id}/documents/{kind}` | Update document | `DocumentUpdate` + `If-Match` | `ProjectDocument` |

**Schemas**:
```python
class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    parent_id: Optional[UUID] = None
    summary: str = Field(default="", max_length=1000)
    tone: Literal["red", "blue", "yellow", "neutral"] = "neutral"
    active: bool = True

class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    parent_id: Optional[UUID] = None  # move in hierarchy
    summary: Optional[str] = Field(default=None, max_length=1000)
    tone: Optional[Literal["red", "blue", "yellow", "neutral"]] = None
    active: Optional[bool] = None
    progress: Optional[int] = Field(default=None, ge=0, le=100)

class Project(BaseModel):
    id: UUID
    user_id: UUID
    parent_id: Optional[UUID]
    name: str
    summary: str
    tone: Literal["red", "blue", "yellow", "neutral"]
    progress: int
    active: bool
    revision: int
    created_at: datetime
    updated_at: datetime

class ProjectDetail(Project):
    children: List[Project] = []
    documents: Dict[str, str] = {}  # kind -> content
    open_task_count: int
    total_estimate_minutes: int

class DocumentUpdate(BaseModel):
    content: str = Field(..., max_length=50000)
```

---

### 3.3 Captures & Diary (`/api/v1/captures`)

| Method | Path | Operation | Request | Response |
|--------|------|-----------|---------|----------|
| POST | `/` | Submit capture | `CaptureCreate` | `CaptureReceipt` |
| GET | `/` | List captures | `?after=&limit=&status=` | `CaptureList` |
| GET | `/{capture_id}` | Get capture | — | `CaptureDetail` |
| GET | `/{capture_id}/extractions` | Get extracted items | — | `ExtractedItemList` |

**Schemas**:
```python
class CaptureCreate(BaseModel):
    raw_text: str = Field(..., min_length=1, max_length=10000)
    source: Literal["Text", "Voice", "CLI", "API"] = "Text"
    timestamp: Optional[datetime] = None  # defaults to now()
    timezone: Optional[str] = None  # IANA tz for deadline normalization
    # capture_id generated server-side (ULID)

class CaptureReceipt(BaseModel):
    capture_id: UUID
    persisted: bool
    timestamp: datetime
    journal_path: Optional[str] = None  # for compatibility
    error: Optional[str] = None

class CaptureDetail(BaseModel):
    id: UUID
    raw_text: str
    source: str
    timestamp: datetime
    timezone: Optional[str]
    processing_status: Literal["pending", "queued", "running", "succeeded", "failed"]
    processing_job_id: Optional[UUID]
    extraction_error: Optional[str]
    created_at: datetime

class ExtractedItem(BaseModel):
    id: UUID
    capture_id: UUID
    extraction_type: Literal["task", "possible_task", "progress", "activity", "decision", "completion", "learning", "problem", "observation"]
    description: str
    project_id: Optional[UUID]
    project_resolution: Literal["existing", "alias", "fuzzy", "new", "ambiguous", "unknown"]
    deadline: Optional[date]
    deadline_resolution: Literal["resolved", "deferred", "none"]
    deadline_raw: Optional[str]
    certainty: float  # 0.0-1.0
    confidence: float  # 0.0-1.0
    source_excerpt: Optional[str]
    temporal_context: Optional[str]
    action_type: str  # VaultAction type name
    action_payload: dict  # serialized fields
    created_at: datetime
```

---

### 3.4 Processing (`/api/v1/processing`)

| Method | Path | Operation | Request | Response |
|--------|------|-----------|---------|----------|
| GET | `/{job_id}` | Get job status | — | `ProcessingJobStatus` |
| POST | `/{job_id}/retry` | Retry failed job | `If-Match` | `ProcessingJobStatus` |

**Schemas**:
```python
class ProcessingJobStatus(BaseModel):
    id: UUID
    capture_id: UUID
    idempotency_key: UUID
    status: Literal["queued", "running", "succeeded", "failed"]
    attempt: int
    last_error: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
```

---

### 3.5 Focus (`/api/v1/focus`)

| Method | Path | Operation | Request | Response |
|--------|------|-----------|---------|----------|
| POST | `/sessions` | Start session | `FocusSessionCreate` | `FocusSession` |
| PATCH | `/sessions/{session_id}` | Update session | `FocusSessionUpdate` + `If-Match` | `FocusSession` |
| POST | `/sessions/{session_id}/complete` | Complete session | `If-Match` | `FocusSession` |
| GET | `/sessions` | List sessions | `?date=&task_id=&after=&limit=` | `FocusSessionList` |
| GET | `/daily/{date}` | Daily aggregate | — | `DailyFocusAggregate` |

**Schemas**:
```python
class FocusSessionCreate(BaseModel):
    task_id: Optional[UUID] = None
    mode: Literal["Focus", "Break"] = "Focus"
    planned_seconds: int = Field(default=1500, ge=60, le=7200)

class FocusSessionUpdate(BaseModel):
    mode: Optional[Literal["Focus", "Break"]] = None
    planned_seconds: Optional[int] = Field(default=None, ge=60, le=7200)

class FocusSession(BaseModel):
    id: UUID
    task_id: Optional[UUID]
    mode: Literal["Focus", "Break"]
    duration_seconds: int
    planned_seconds: int
    completed: bool
    started_at: datetime
    ended_at: Optional[datetime]
    date: date
    revision: int

class DailyFocusAggregate(BaseModel):
    date: date
    total_focus_seconds: int
    total_break_seconds: int
    session_count: int
    completed_sessions: int
    by_task: List[TaskFocusSummary]  # task_id, title, project, seconds

class TaskFocusSummary(BaseModel):
    task_id: UUID
    title: str
    project_name: str
    total_seconds: int
```

---

### 3.6 Statistics (`/api/v1/stats`)

| Method | Path | Operation | Request | Response |
|--------|------|-----------|---------|----------|
| GET | `/overview` | Dashboard overview | `?weeks=12` | `StatsOverview` |
| GET | `/activity-grid` | Year heatmap | `?year=` | `ActivityGrid` |
| GET | `/streaks` | Streak data | — | `StreakData` |
| GET | `/project-breakdown` | Project share | `?days=35` | `ProjectBreakdown` |
| GET | `/flavor-mix` | Task mood distribution | `?days=35` | `FlavorMix` |

**Schemas**:
```python
class StatsOverview(BaseModel):
    total_tasks_done: int
    total_pomodoros: int  # focus sessions completed
    current_streak_days: int
    best_streak_days: int
    best_day_completions: int
    average_daily_completions: float

class ActivityGrid(BaseModel):
    year: int
    weeks: List[List[ActivityCell]]  # 52 x 7

class ActivityCell(BaseModel):
    tone: Literal["empty", "red", "blue", "yellow", "cream"]
    date: date
    is_today: bool = False

class StreakData(BaseModel):
    current: int
    best: int
    daily_completions: List[int]  # last 35 days

class ProjectBreakdown(BaseModel):
    items: List[ProjectStat]
    
class ProjectStat(BaseModel):
    project_id: UUID
    project_name: str
    tone: str
    tasks_done: int
    share_percent: float

class FlavorMix(BaseModel):
    categories: List[FlavorCategory]
    
class FlavorCategory(BaseModel):
    name: Literal["Frog", "Deep", "Spark", "Errand", "Plain"]
    count: int
    color: str  # hex or tone name
```

---

### 3.7 Files (`/api/v1/files`)

| Method | Path | Operation | Request | Response |
|--------|------|-----------|---------|----------|
| POST | `/presign-upload` | Get presigned PUT URL | `PresignUploadRequest` | `PresignUploadResponse` |
| POST | `/presign-download` | Get presigned GET URL | `PresignDownloadRequest` | `PresignDownloadResponse` |
| POST | `/` | Register uploaded file | `FileRegister` | `File` |
| GET | `/` | List files | `?project_id=&purpose=` | `FileList` |
| DELETE | `/{file_id}` | Delete file (S3 + DB) | `If-Match` | `204` |

**Schemas**:
```python
class PresignUploadRequest(BaseModel):
    filename: str
    content_type: str
    size_bytes: int = Field(..., le=50_000_000)  # 50MB
    purpose: Literal["upload", "draft", "generated", "export"]
    project_id: Optional[UUID] = None

class PresignUploadResponse(BaseModel):
    upload_url: str  # presigned PUT, expires 15 min
    file_id: UUID
    s3_key: str
    expires_at: datetime

class FileRegister(BaseModel):
    file_id: UUID  # from presign response
    original_filename: str

class File(BaseModel):
    id: UUID
    project_id: Optional[UUID]
    s3_key: str
    bucket: str
    content_type: str
    size_bytes: int
    original_filename: str
    purpose: str
    created_at: datetime
```

---

### 3.8 Search (`/api/v1/search`)

| Method | Path | Operation | Request | Response |
|--------|------|-----------|---------|----------|
| GET | `/` | Full-text search | `?q=&type=&project_id=&after=&limit=` | `SearchResults` |

**Schemas**:
```python
class SearchResults(BaseModel):
    tasks: List[TaskSearchHit]
    captures: List[CaptureSearchHit]
    documents: List[DocumentSearchHit]

class TaskSearchHit(BaseModel):
    task: Task
    rank: float
    highlights: List[str]

class CaptureSearchHit(BaseModel):
    capture: CaptureDetail
    rank: float
    highlights: List[str]

class DocumentSearchHit(BaseModel):
    project_id: UUID
    project_name: str
    kind: str
    rank: float
    highlights: List[str]
```

---

### 3.9 Export/Import (`/api/v1/export`, `/api/v1/import`)

| Method | Path | Operation | Request | Response |
|--------|------|-----------|---------|----------|
| GET | `/export/project/{project_id}` | Export project as markdown zip | — | `FileResponse` (zip) |
| GET | `/export/all` | Export all data as markdown zip | — | `FileResponse` (zip) |
| POST | `/import/project` | Import project from markdown | Multipart form (zip) | `ImportResult` |

**Schemas**:
```python
class ImportResult(BaseModel):
    project_id: UUID
    created: int  # new records
    updated: int  # existing records updated
    errors: List[str]
```

---

### 3.10 Devices (`/api/v1/devices`)

| Method | Path | Operation | Request | Response |
|--------|------|-----------|---------|----------|
| POST | `/` | Register device | `DeviceRegister` | `Device` |
| GET | `/` | List my devices | — | `DeviceList` |
| GET | `/{device_id}` | Get device | — | `Device` |
| PATCH | `/{device_id}` | Update device | `DeviceUpdate` + `If-Match` | `Device` |
| POST | `/{device_id}/revoke` | Revoke device | — | `204` |
| GET | `/{device_id}/capabilities` | Get capabilities | — | `DeviceCapabilities` |

**Schemas**:
```python
class DeviceRegister(BaseModel):
    device_id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]{8,64}$")  # client-generated stable ID
    name: str = Field(..., min_length=1, max_length=100)
    platform: Literal["linux", "darwin", "windows"]
    capabilities: Dict[str, bool] = Field(default_factory=dict)  # {"terminal": true, "filesystem": true, "agent": false}
    public_key: Optional[str] = None  # for mTLS

class DeviceUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    capabilities: Optional[Dict[str, bool]] = None

class Device(BaseModel):
    id: UUID
    device_id: str
    name: str
    platform: str
    capabilities: Dict[str, bool]
    status: Literal["online", "offline", "stale"]
    last_seen_at: Optional[datetime]
    registered_at: datetime
    revoked_at: Optional[datetime]

class DeviceCapabilities(BaseModel):
    terminal: bool
    filesystem: bool
    agent: bool
    allowed_paths: List[str]  # e.g., ["/home/user/projects/*"]
```

---

### 3.11 Companion RPC (`/api/v1/companion`)

**Note**: Primary companion communication is via **WebSocket** (`/ws/companion`). HTTP endpoints are for session management only.

| Method | Path | Operation | Request | Response |
|--------|------|-----------|---------|----------|
| POST | `/sessions` | Create companion session | `CompanionSessionCreate` | `CompanionSession` |
| GET | `/sessions` | List active sessions | — | `CompanionSessionList` |
| POST | `/sessions/{session_id}/revoke` | Revoke session | — | `204` |
| GET | `/devices/{device_id}/rpc/manifest` | Get RPC manifest | — | `RPCManifest` |

**WebSocket**: `/ws/companion?token=<session_token>`
- Frames: JSON-RPC 2.0 over WebSocket
- Methods: `pty_spawn`, `pty_write`, `pty_read`, `pty_resize`, `pty_kill`, `pty_list`, `fs_read`, `fs_list`, `fs_stat`, `fs_write`, `fs_mkdir`, `ps_list`, `ps_exec`, `agent_spawn`, `agent_status`
- Each call: `{jsonrpc: "2.0", id: <uuid>, method: "...", params: {...}}`
- Response: `{jsonrpc: "2.0", id: <uuid>, result: {...}}` or `{jsonrpc: "2.0", id: <uuid>, error: {...}}`

**Schemas**:
```python
class CompanionSessionCreate(BaseModel):
    device_id: str
    capabilities_requested: List[Literal["terminal.read", "terminal.write", "terminal.control", "filesystem.read", "filesystem.write", "process.list", "process.exec", "agent.orchestrate"]]
    ttl_minutes: int = Field(default=15, ge=5, le=60)

class CompanionSession(BaseModel):
    id: UUID
    token: str  # session token for WebSocket auth
    device_id: str
    capabilities_granted: List[str]
    expires_at: datetime
    ws_url: str  # e.g., "wss://api.pos-deku.com/ws/companion?token=..."

class RPCManifest(BaseModel):
    methods: List[RPCMethod]

class RPCMethod(BaseModel):
    name: str
    description: str
    params_schema: dict  # JSON Schema
    result_schema: dict
    requires_approval: bool
```

---

### 3.12 Workspaces (`/api/v1/workspaces`)

| Method | Path | Operation | Request | Response |
|--------|------|-----------|---------|----------|
| POST | `/` | Provision workspace | `WorkspaceProvision` | `WorkspaceInstance` |
| GET | `/` | List workspaces | `?status=&after=&limit=` | `WorkspaceList` |
| GET | `/{instance_id}` | Get workspace | — | `WorkspaceInstance` |
| POST | `/{instance_id}/terminate` | Terminate workspace | — | `WorkspaceInstance` |
| GET | `/{instance_id}/logs` | Get provisioning logs | — | `WorkspaceLogs` |
| GET | `/templates` | List templates | — | `WorkspaceTemplateList` |
| POST | `/templates` | Create template | `WorkspaceTemplateCreate` | `WorkspaceTemplate` |

**Schemas**:
```python
class WorkspaceProvision(BaseModel):
    template_id: UUID
    project_id: Optional[UUID] = None
    github_repo: str  # "owner/repo"
    github_branch: str = "main"
    instance_type: Literal["t3.medium", "t3.large", "t3.xlarge"] = "t3.medium"
    max_runtime_minutes: int = Field(default=240, ge=30, le=1440)
    idle_timeout_minutes: int = Field(default=30, ge=5, le=240)

class WorkspaceInstance(BaseModel):
    id: UUID
    template_id: UUID
    project_id: Optional[UUID]
    github_repo: str
    github_branch: str
    ec2_instance_id: Optional[str]
    ec2_instance_type: str
    status: Literal["pending", "provisioning", "running", "stopping", "terminated", "error"]
    connection_info: Optional[dict]  # {"ws_url": "...", "instance_ip": "...", "port": 22}
    cost_limit_usd: float
    accumulated_cost_usd: float
    started_at: Optional[datetime]
    terminated_at: Optional[datetime]
    created_at: datetime

class WorkspaceTemplate(BaseModel):
    id: UUID
    name: str
    description: str
    template_type: Literal["devcontainer", "nix", "script"]
    s3_prefix: str
    default_instance_type: str
    max_runtime_minutes: int
    idle_timeout_minutes: int
    created_at: datetime
```

---

### 3.13 Sync Endpoints (Offline Reconciliation)

| Method | Path | Operation | Request | Response |
|--------|------|-----------|---------|----------|
| POST | `/sync/operations` | Submit offline operations | `SyncOperationBatch` | `SyncOperationResult` |
| GET | `/sync/status` | Get pending conflicts | — | `SyncStatus` |
| POST | `/sync/conflicts/{conflict_id}/resolve` | Resolve conflict | `ConflictResolution` | `200` |

**Schemas**:
```python
class SyncOperation(BaseModel):
    operation_id: UUID
    entity_type: Literal["task", "project", "capture", "focus_session", "project_document"]
    entity_id: UUID
    operation: Literal["create", "update", "delete", "complete"]
    base_revision: int
    client_updated_at: datetime
    payload: dict  # entity-specific

class SyncOperationBatch(BaseModel):
    device_id: str
    operations: List[SyncOperation]

class SyncOperationResult(BaseModel):
    results: List[SyncOperationOutcome]

class SyncOperationOutcome(BaseModel):
    operation_id: UUID
    status: Literal["applied", "conflict", "rejected", "duplicate"]
    server_revision: Optional[int] = None
    conflict_id: Optional[UUID] = None
    message: Optional[str] = None

class ConflictResolution(BaseModel):
    conflict_id: UUID
    resolution: Literal["server_wins", "client_wins", "merge"]
    merged_payload: Optional[dict] = None  # if merge
```

---

### 3.14 Session Management (`/api/v1/sessions`)

| Method | Path | Operation | Request | Response |
|--------|------|-----------|---------|----------|
| GET | `/` | List my sessions | `?status=&after=&limit=` | `SessionList` |
| POST | `/{session_id}/revoke` | Revoke session | — | `204` |
| GET | `/audit` | Get session audit log | `?session_id=&after=&limit=` | `SessionAuditList` |

**Schemas**:
```python
class Session(BaseModel):
    id: UUID
    session_type: Literal["companion_terminal", "cloud_workspace_terminal"]
    device_id: Optional[str]
    workspace_instance_id: Optional[UUID]
    capabilities_granted: List[str]
    status: Literal["active", "closed", "revoked", "expired"]
    expires_at: datetime
    created_at: datetime
    closed_at: Optional[datetime]

class SessionAuditEntry(BaseModel):
    id: UUID
    session_id: UUID
    action: Literal["session_start", "rpc_call", "session_end", "revoked"]
    capability: Optional[str]
    rpc_method: Optional[str]
    rpc_params: Optional[dict]  # redacted
    result: Optional[Literal["success", "error", "denied"]]
    client_ip: Optional[str]
    created_at: datetime
```

---

## 4. Health & Meta

| Method | Path | Operation |
|--------|------|-----------|
| GET | `/health` | Liveness (200 OK) |
| GET | `/ready` | Readiness (DB, SQS, S3, session broker reachable) |
| GET | `/version` | Build version, git SHA |

---

## 5. WebSocket Endpoints

| Path | Protocol | Purpose |
|------|----------|---------|
| `/ws/companion` | JSON-RPC 2.0 | Companion daemon ↔ session broker (terminal, fs, process, agent) |
| `/ws/workspace/{instance_id}` | xterm.js compatible | Cloud workspace terminal (portal ↔ instance) |
| `/ws/realtime` | JSON | Real-time updates (task changes, processing status, presence) — v2 |

---

## 6. Test Contracts

Each endpoint group has contract tests:
- `tests/contract/test_tasks.py` — CRUD, optimistic lock, 409 on conflict
- `tests/contract/test_projects.py` — Hierarchy, documents, move validation
- `tests/contract/test_captures.py` — Persist-first, processing status
- `tests/contract/test_processing.py` — Idempotency, retry, DLQ
- `tests/contract/test_focus.py` — Session lifecycle, aggregates
- `tests/contract/test_stats.py` — Derived metrics match SQL
- `tests/contract/test_files.py` — Presign round-trip, S3 mock
- `tests/contract/test_search.py` — FTS ranking, filters
- `tests/contract/test_sync.py` — Offline reconciliation, idempotency, conflict
- `tests/contract/test_devices.py` — Registration, heartbeat, revocation
- `tests/contract/test_companion.py` — Session creation, RPC manifest, capability checks
- `tests/contract/test_workspaces.py` — Provisioning, lifecycle, cost limits

Run: `pytest tests/contract/ -v`