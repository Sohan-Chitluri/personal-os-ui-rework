# Personal OS TUI Design Document

## Vision
A persistent conversational terminal interface — the user talks to their "Personal OS" like a teammate. Skills, processors, routing, and vault management are completely invisible. Keyboard-first, minimal, responsive.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        pos (entry point)                         │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                    TUI Application (Textual)               │  │
│  │  ┌──────────────┐  ┌──────────────────────────────────┐   │  │
│  │  │  Sidebar     │  │  Main Chat Workspace             │   │  │
│  │  │  (Nav)       │  │  ┌────────────────────────────┐  │   │  │
│  │  │              │  │  │  Conversation History      │  │   │  │
│  │  │  [1] Projects│  │  │  (scrollable, persistent)  │  │   │  │
│  │  │  [2] Tasks   │  │  │                            │  │   │  │
│  │  │  [3] Journal │  │  │  ┌──────────────────────┐  │  │   │  │
│  │  │  [4] Search  │  │  │  │  Input Buffer        │  │  │   │  │
│  │  │  [5] Calendar│  │  │  │  (multi-line, edit)  │  │  │   │  │
│  │  │  [6] Settings│  │  │  └──────────────────────┘  │  │   │  │
│  │  │              │  │  └────────────────────────────┘  │   │  │
│  │  └──────────────┘  └──────────────────────────────────┘   │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Background: Async Processor → Vault Manager → Skills           │
└─────────────────────────────────────────────────────────────────┘
```

---

## Entry Points

| Command | Behavior |
|---------|----------|
| `pos` | Launch TUI (default) |
| `pos chat "text"` | Quick message → TUI opens with message pre-filled |
| `pos note "text"` | Instant capture → background append to journal, no TUI |
| `pos voice` | Voice capture → transcribe → background append |
| `pos today` | Open TUI at Journal panel scrolled to today |
| `pos search "query"` | Open TUI at Search panel with results |
| `pos project <name>` | Open TUI at Projects panel focused on project |

---

## TUI Layout (Textual)

### Main Screen: Split View
```
┌─ Sidebar (25%) ──────────────────────┬─ Chat Workspace (75%) ────────────────────┐
│  PERSONAL OS                         │  ┌─ Conversation ────────────────────────┐  │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━   │  │                                       │  │
│  ◉ Projects     [3 active]           │  │  You: Fixed the streaming bug         │  │
│    ▸ Maestro (Router)                │  │  OS: ✅ Logged. Updated Maestro/      │  │
│    ▸ Drone (STM32)                   │  │     Progress.md (+3h). Any blockers?  │  │
│    ▸ BlindGuard                      │  │                                       │  │
│                                      │  │  You: Need to finish SSE lifetime     │  │
│  ◉ Tasks           [5 open]          │  │  OS: Added to Maestro/Tasks.md.       │  │
│    ▸ SSE lifetime fix (Maestro)      │  │  Want me to schedule a review?        │  │
│    ▸ VLSI assignment (due Thu)       │  │                                       │  │
│    ▸ OpenRouter provider             │  │  ────────────────────────────────────  │  │
│                                      │  │  You: _                               │  │
│  ◉ Journal                          │  └────────────────────────────────────────┘  │
│    ▸ Today (3 entries)               │  [Tab] Switch panel  [Esc] Sidebar  [Ctrl+L] Clear │
│    ▸ Yesterday                       │                                              │
│    ▸ This week                       │                                              │
│                                      │                                              │
│  ◉ Search                           │                                              │
│    ▸ "streaming bug" (4 results)     │                                              │
│    ▸ "router config" (2 results)     │                                              │
│                                      │                                              │
│  ◉ Calendar                          │                                              │
│    ▸ Today: 2 blocks                 │                                              │
│    ▸ This week                       │                                              │
│                                      │                                              │
│  ⚙ Settings                          │                                              │
│    ▸ Vault: ~/projects/personal-os   │                                              │
│    ▸ Model: local (llama.cpp)        │                                              │
│    ▸ Voice: whisper.cpp (base)       │                                              │
└──────────────────────────────────────┴──────────────────────────────────────────────┘
```

### Panel Details

#### 1. Projects Panel
- Tree view: Project → Files (Progress.md, Tasks.md, Architecture.md, etc.)
- Inline status: 🟢 Active / 🟡 Planning / 🔴 Blocked / ⚪ Archived
- Quick actions (on selection): Open, New Task, View Progress, Archive

#### 2. Tasks Panel
- Unified view across ALL projects
- Grouped by: Project / Due Date / Status / #tag
- Keyboard: `j/k` navigate, `x` toggle complete, `e` edit, `d` set due date

#### 3. Journal Panel
- Date navigator (calendar heatmap style)
- Today expanded by default
- Entry preview with inline actions: Process, Link to Project, Convert to Task

#### 4. Search Panel
- Live search as you type (debounced 150ms)
- Filters: `project:`, `type:`, `tag:`, `date:`
- Results grouped by file, with context snippets
- `Enter` opens in context, `p` pins to sidebar

#### 5. Calendar Panel
- Week view by default, `w`/`m` for week/month
- Time blocks from journal entries with timestamps
- Drag-to-create (future), click to jump to journal entry

#### 6. Settings Panel
- Vault path, model config, voice config, keybindings, theme
- All persisted to `~/.config/personal-os/config.toml`

---

## Chat Workspace — The Primary Interface

### Conversation Flow
```
You: I spent 3 hours fixing the streaming bug in Maestro router

OS: ✅ Logged to journal. Updated Maestro/Progress.md (+3h).
    Also noticed "SSE lifetime" mentioned — added to Maestro/Tasks.md.
    Want me to schedule a review session?

You: yes, tomorrow 2pm

OS: 📅 Added calendar block: "Maestro streaming review" tomorrow 14:00.
    Anything else for today?

You: /search router config

OS: [Search panel opens with results, sidebar highlights Search]
    1. Maestro/Architecture.md:12 - "Router config uses OpenAI-compatible..."
    2. Maestro/Decisions.md:5 - "Decision: Use Axum for HTTP..."
    3. Journal/2026-07-13.md: - "Updated router config for OpenRouter..."
```

### Special Commands (slash-prefixed, invisible to user)
| Command | Action |
|---------|--------|
| `/search <query>` | Open search panel |
| `/project <name>` | Focus project panel |
| `/task <text>` | Quick task create |
| `/cal <date>` | Jump to calendar |
| `/voice` | Start voice capture |
| `/help` | Show keybindings |
| `/settings` | Open settings panel |

---

## Keyboard Navigation (Yazi/Lazygit/btop inspired)

### Global (always work)
| Key | Action |
|-----|--------|
| `Tab` | Cycle focus: Sidebar → Chat → Input |
| `Shift+Tab` | Reverse cycle |
| `Esc` | Focus sidebar / Close modal / Cancel |
| `Ctrl+L` | Clear chat (keeps history) |
| `Ctrl+Q` | Quit (confirm if unsent input) |
| `?` | Show keybinding help overlay |

### Sidebar (when focused)
| Key | Action |
|-----|--------|
| `j/k` / `↑/↓` | Navigate items |
| `h/l` / `←/→` | Collapse/expand section |
| `Enter` | Open/activate selection |
| `1-6` | Jump to panel (1=Projects, 2=Tasks...) |
| `p` | Pin current search/project/task |
| `/` | Focus search input in sidebar |

### Chat Workspace (when focused)
| Key | Action |
|-----|--------|
| `↑/↓` | Scroll conversation history |
| `PgUp/PgDn` | Page scroll |
| `Home/End` | Jump to top/bottom |
| `Ctrl+U` | Clear input buffer |
| `Ctrl+K` | Delete line |
| `Enter` | Send message |
| `Shift+Enter` | New line in input |
| `Ctrl+R` | Reply to last OS message (thread) |

### Input Buffer (multi-line editor)
- `Ctrl+E` / `Ctrl+A` - End/Start of line
- `Alt+←/→` - Word jump
- `Ctrl+W` - Delete word back
- `Ctrl+Y` - Paste from clipboard
- `Ctrl+V` - Paste (terminal dependent)

### Panel-Specific (when panel active in main area)
| Panel | Keys |
|-------|------|
| Projects | `n` new project, `a` archive, `o` open in editor |
| Tasks | `x` toggle, `e` edit, `d` due date, `t` tag, `p` project |
| Journal | `p` process entry, `l` link to project, `t` convert to task |
| Search | `Enter` open, `p` pin, `f` filter menu |
| Calendar | `←/→` day, `w` week, `m` month, `n` new block |

---

## Background Processing (Invisible)

### Architecture
```
User Input (chat/voice/note)
       │
       ▼
┌──────────────────┐
│  Ingestion Queue │  (async, bounded, persists to disk)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  NLP Router      │  → classifies: progress/decision/task/learning/observation/deadline
│  (local LLM or   │
│   rule-based)    │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Vault Manager   │  → atomic file operations, conflict resolution, git commits
│  (transactional) │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Indexer         │  → updates search index (tantivy), project graph, calendar
└──────────────────┘
```

### Processing Guarantees
- **Journal append** = immediate, never blocks UI
- **Extraction** = async, <500ms typical, user sees "✅ Logged" instantly
- **Vault writes** = batched, atomic per file, crash-safe
- **Search index** = eventual consistency (background), UI shows "Indexing..." badge

---

## Data Models

### Conversation Message
```python
@dataclass
class Message:
    id: str              # ULID
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: datetime
    metadata: dict       # extractions, linked projects, tasks created, etc.
    status: Literal["sent", "processing", "complete", "error"]
```

### Project Index Entry
```python
@dataclass
class ProjectIndex:
    name: str
    path: Path
    status: Literal["active", "planning", "blocked", "archived"]
    last_activity: datetime
    open_tasks: int
    total_hours: float
    health: Literal["green", "yellow", "red"]  # computed
    next_milestone: str | None
```

### Unified Task
```python
@dataclass
class Task:
    id: str
    project: str
    title: str
    status: Literal["open", "in_progress", "done", "blocked"]
    due: datetime | None
    tags: list[str]
    source: Literal["chat", "journal", "manual", "voice"]
    created: datetime
    updated: datetime
```

---

## Configuration (`~/.config/personal-os/config.toml`)

```toml
[vault]
path = "~/projects/personal-os"
git_auto_commit = true

[ui]
theme = "dark"        # dark, light, auto
compact_mode = false  # reduced padding
show_timestamps = true
sidebar_width = 25

[processing]
model = "local"       # local, openrouter, anthropic
auto_process = true
extraction_timeout_ms = 5000

[voice]
enabled = true
model = "base"        # tiny, base, small, medium, large-v3
language = "en"
push_to_talk_key = "F13"  # or "ctrl+space"

[shortcuts]
# All keybindings customizable
leader = "Space"
```

---

## Implementation Plan

### Phase 1: TUI Skeleton (Textual)
1. `pos` → launches Textual app
2. Two-pane layout: Sidebar + Chat Workspace
3. Basic chat: input → echo → history scroll
4. Sidebar navigation (6 panels, keyboard driven)

### Phase 2: Chat Intelligence
1. Connect existing `daily_stream.Processor` to chat input
2. Background task queue (asyncio + persist to SQLite)
3. Real-time vault updates reflected in sidebar
4. Slash commands (`/search`, `/project`, etc.)

### Phase 3: Panel Implementations
1. **Projects** — tree view from vault scan
2. **Tasks** — unified Tasks.md parser across projects
3. **Journal** — date navigator + entry cards
4. **Search** — integrate `search_vault` skill + tantivy index
5. **Calendar** — parse timestamps from journal, render week view
6. **Settings** — TOML config editor

### Phase 4: Voice & Polish
1. `pos voice` → whisper.cpp → background ingestion
2. Push-to-talk global hotkey (via `uhid`/`evdev` or desktop portal)
3. Themes, keybinding editor, compact mode
4. Notifications (system + in-app toast)

### Phase 5: Intelligence
1. Local LLM for extraction (llama.cpp) — replace regex
2. Proactive suggestions: "You haven't touched Drone in 3 days"
3. Weekly review generation
4. Project health scoring

---

## Technical Stack

| Layer | Technology |
|-------|------------|
| TUI Framework | **Textual** (Python) — reactive, async, CSS styling |
| Async Runtime | `asyncio` + `anyio` for structured concurrency |
| Search Index | **tantivy** (Rust via Python bindings) or `whoosh` |
| Config | `tomllib`/`tomli_w` |
| Vault Ops | `pathlib` + atomic writes + `gitpython` |
| Voice | `whisper.cpp` subprocess (existing) |
| Local LLM | `llama-cpp-python` (optional, for extraction) |
| Notifications | `notify-send` / `dbus` / textual notifications |

---

## File Structure (New)

```
personal-os/
├── pos                      # Entry point (unchanged)
├── src/
│   └── personal_os/
│       ├── __init__.py
│       ├── tui/
│       │   ├── __init__.py
│       │   ├── app.py           # Textual App
│       │   ├── screens/
│       │   │   ├── __init__.py
│       │   │   ├── main.py      # Main screen (sidebar + chat)
│       │   │   └── help.py      # Keybinding overlay
│       │   ├── widgets/
│       │   │   ├── __init__.py
│       │   │   ├── sidebar.py
│       │   │   ├── chat_workspace.py
│       │   │   ├── conversation.py
│       │   │   ├── input_buffer.py
│       │   │   ├── panels/
│       │   │   │   ├── __init__.py
│       │   │   │   ├── projects.py
│       │   │   │   ├── tasks.py
│       │   │   │   ├── journal.py
│       │   │   │   ├── search.py
│       │   │   │   ├── calendar.py
│       │   │   │   └── settings.py
│       │   │   └── toast.py
│       │   ├── css/
│       │   │   └── app.tcss
│       │   └── bindings.py
│       ├── core/
│       │   ├── __init__.py
│       │   ├── vault.py         # VaultManager (atomic ops, git)
│       │   ├── ingestion.py     # IngestionQueue, NLPRouter
│       │   ├── index.py         # SearchIndex (tantivy)
│       │   ├── models.py        # Dataclasses
│       │   └── config.py        # Config loading
│       ├── skills/              # Refactored from .hermes/skills
│       │   ├── __init__.py
│       │   ├── daily_stream.py  # Processor → Extractor
│       │   ├── search_vault.py
│       │   ├── voice_capture.py
│       │   ├── project_ops.py
│       │   └── review.py
│       └── cli/
│           ├── __init__.py
│           ├── main.py          # pos CLI (Typer)
│           ├── chat.py          # pos chat "msg"
│           ├── note.py          # pos note "text"
│           ├── voice.py         # pos voice
│           └── today.py         # pos today
├── tests/
├── pyproject.toml
└── README.md
```

---

## Migration Strategy

1. **Keep `pos` working** — CLI commands delegate to new `src/personal_os` modules
2. **Extract skills** — Move `.hermes/skills/*` → `src/personal_os/skills/` as plain Python modules
3. **Build TUI alongside** — Textual app in `src/personal_os/tui/`
4. **Switch default** — `pos` without args → TUI; `pos <cmd>` → CLI shortcut
5. **Deprecate old** — Remove `.hermes/skills` dependency, update wrapper scripts

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Textual over `curses`/`urwid`** | Reactive, CSS styling, async-native, active community |
| **Chat as primary, panels secondary** | Matches mental model of "talking to OS", not "operating tool" |
| **Background queue + eventual consistency** | UI never blocks; user types continuously |
| **Slash commands not flags** | Discoverable in chat, no memorization |
| **Sidebar always visible** | Context awareness — projects/tasks/journal always one `Tab` away |
| **No modes** | Yazi-style: same keys work everywhere, context determines action |
| **Atomic vault writes** | Crash-safe, git-friendly, no partial states |

---

## Success Metrics

- **Cold start → first capture** < 2 seconds
- **Chat latency** (input → "✅ Logged") < 100ms
- **Search results** < 200ms (local index)
- **Memory** < 100MB idle
- **Zero CLI commands needed** for daily workflow