# Personal OS v0.2 — Architecture Baseline (Post-Milestone 3.5)

> **Reference architecture for v0.2**. Future milestones **extend** this architecture; they do not restructure it.

---

## 1. Capture Contract (Mandatory, Every Input Path)

```text
Capture → Persist (append_journal_entry, timestamped, atomic, git-committed)
       → Queue processing (async, best-effort)
       → Update vault → UI feedback
```

- **Persist FIRST, always**. Raw captures are never lost even if processing fails.
- Canonical impl: `src/personal_os/core/capture.py`
  - `persist_capture(raw, source, ...) → CaptureReceipt` — stage 1 only; never raises
  - `capture(...)` — persist then queue on BackgroundProcessor (non-blocking)
  - `capture_and_process(...)` — persist then run inline, returns `(receipt, result)`; used by CLI
- **All input paths** (TUI text, TUI voice, `pos note`, `pos voice`) **must** route through this module. Never call `process_entry` directly.

---

## 2. Workspace Model

### Hierarchy

```
Workspace (TodayView, ProjectsView, …)      ← switched by ViewRouter
    └── Layout (vertical/horizontal regions)
        └── Panels (labelled regions)
            └── Components (DashboardPanel, ConversationPanel, MarkdownRenderer, …)
```

- **Router** switches **workspaces only** — knows nothing about layouts/panels/components.
- **WorkspaceView** composes reusable **Components** and passes each a **child WorkspaceContext**.
- Components are **embeddable leaves** — never in `VIEW_REGISTRY`, never reachable from navigator.

### TodayView (First Composed Workspace)

```
TodayView (view_id="today")
├── Dashboard region        → DashboardPanel(context=child(breadcrumbs=["Today","Dashboard"]))
├── Recent Captures region  → Placeholder (child context)
├── Pending Processing      → Placeholder (child context)
└── Current Project region  → ConversationPanel(context=child(breadcrumbs=["Today","Current Project"]))
```

---

## 3. Navigation Model

### NavigationState (Shell Only)

```python
@dataclass
class NavigationState:
    active_view: str = "today"           # current workspace id
    history: List[str] = []              # back stack (most recent last)
    forward_stack: List[str] = []        # forward stack
```

**Responsibilities**: workspace switching, back/forward, history limit (100).

**Explicitly NOT here** (reserved for future workspace-local navigation):
- `selected_project`, `selected_document`, `focused_panel`, `breadcrumbs`

### NavigatorSidebar

- Persistent Yazi-style sidebar listing `NAV_ITEMS` in order.
- **Selection is SILENT**: posts `NavigateRequested(view_id)` only — no chat output, no AI processing.
- Consumes `NavigationState` via `highlight_view()`; does not own it.

### Keyboard Navigation

| Key | Action |
|-----|--------|
| 1–9 | Jump to workspace (1=today, 2=projects, …) |
| j/k / arrows | Move cursor in sidebar |
| Enter / l | Select workspace |
| Ctrl+B | Toggle sidebar |
| Back/Forward | Browser-style (via `NavigationState.back()` / `forward()`) |

---

## 4. ViewRouter Responsibilities

```python
class ViewRouter(Container):
    def __init__(self, services: AppServices): ...
    def show(self, view_id: str) -> bool: ...
    async def refresh_active(self) -> None: ...
```

- **Single active WorkspaceView** at a time (center pane).
- **Lazy instantiation** from `VIEW_REGISTRY` — views created on first show.
- **Lifecycle hooks**: `on_activate()`, `on_deactivate()`, `refresh_content()`.
- **Context injection**: `view.context = services.build_context(nav, view_id)` on every `show()`.
- **Silent by design** — no messages, no processing, pure view swap.
- **Does not own navigation** — derives active view from `NavigationState` via App.

---

## 5. WorkspaceContext Responsibilities

```python
@dataclass
class WorkspaceContext:
    workspace_id: str = ""
    active_project: Optional[str] = None
    active_document: Optional[Path] = None
    breadcrumbs: List[str] = []
    mode: str = "explore"
    vault: Any = None
    config: Any = None

    def child(self, **overrides) -> "WorkspaceContext": ...
```

### Purpose

- **Components depend on WorkspaceContext, NOT global app state** — keeps them reusable and testable.
- Carries workspace-scoped state: active project, active document, breadcrumbs, mode.
- Provides read-only access to shared services (`vault`, `config`).

### Child Contexts (Standard Mechanism)

```python
# In TodayView.compose()
dashboard_ctx = root_ctx.child(breadcrumbs=["Today", "Dashboard"], mode="explore")
yield DashboardPanel(context=dashboard_ctx)
```

> **Components should NEVER share the root WorkspaceContext instance directly. Use `child()` to create a scoped context for each component.**

### ContextFactory (Single Authoritative Creator)

```python
class ContextFactory:
    def __init__(self, vault, config): ...
    def build(self, nav_state) -> WorkspaceContext:
        return WorkspaceContext(
            workspace_id=nav_state.active_view,
            mode="explore",
            vault=self.vault,
            config=self.config,
            # Future: active_project=nav_state.selected_project, …
        )
```

- App owns **one** `ContextFactory`.
- `AppServices.build_context(nav_state, workspace_id)` is the **single path** for context creation (called by `ViewRouter.show()`).
- Context is **refreshed on every workspace switch** — no stale context possible.

---

## 6. AppServices Responsibilities

```python
@dataclass
class AppServices:
    vault: Any
    config: Any
    processor: Any
    context_factory: ContextFactory
    nav: NavigationState

    @classmethod
    def from_app(cls, vault, config, processor, context_factory, nav): ...

    def build_context(self, nav_state, workspace_id): ...
```

### Purpose

- **Aggregates shared services** to reduce constructor parameter growth.
- **Clean injection point** for tests (mock `AppServices` instead of 5 separate args).
- **Single context creation path** via `build_context()`.

### Consumers

- `PersonalOSTUI` — creates one instance in `__init__`.
- `ViewRouter` — receives via `__init__(services: AppServices)`.
- `WorkspaceView` — receives via `__init__(services: AppServices)`.
- Tests — can inject a fake `AppServices`.

---

## 7. Component Architecture

### Base Class

```python
class Component(Vertical):
    component_id: str = "component"
    title: str = "Component"
    
    def __init__(self, context: Optional[WorkspaceContext] = None, **kwargs): ...
    def set_context(self, context: WorkspaceContext) -> None: ...
    async def refresh_content(self) -> None: ...
```

### Existing Components

| Component | `component_id` | Purpose |
|-----------|----------------|---------|
| `DashboardPanel` | `dashboard-panel` | Embeddable dashboard surface (placeholder) |
| `ConversationPanel` | `conversation-panel` | Embeddable conversation surface (placeholder) — **NOT a workspace** |
| `MarkdownRenderer` | (planned) | `load_path()` / `load_text()` interface (no engine yet) |

### Component Rules

1. **Never in `VIEW_REGISTRY`** — router never switches to a component.
2. **Never reachable from navigator** — sidebar lists workspaces only.
3. **Depend only on `WorkspaceContext`** — no global app references.
4. **Receives context via constructor or `set_context()`** — typically a `child()` context from parent workspace.
5. **Placeholder components** yield a `Static` with `classes="component-placeholder"`.

---

## 8. Status Bar Ownership

- **Owned by the App shell** — created in `App.compose()`, lives at bottom of screen.
- **Not owned by any view** — persists across workspace switches.
- Displays: `mode · workspace   proc: status   hints`
- Updated by App via:
  - `status.set_mode("Explore" | "Capture" | …)`
  - `status.set_workspace("Today" | "Projects" | …)`
  - `status.set_processor("idle" | "processing" | …)`
  - `status.set_hints("…")`

---

## 9. Shell vs Workspace-Local Navigation

| Level | Owned By | Current Fields | Future Fields |
|-------|----------|----------------|---------------|
| **Shell** | `NavigationState` | `active_view`, `history`, `forward_stack` | — |
| **Workspace-Local** | `WorkspaceContext` | `breadcrumbs`, `mode` | `active_project`, `active_document`, `focused_panel`, `selected_project`, `selected_document` |

### Invariants

1. **NavigationState never gains workspace-local fields** — shell/workspace boundary stays clean.
2. **WorkspaceContext is the home for workspace-local navigation** — additive, no refactor needed.
3. **Child contexts inherit workspace_id and shared services** — scoping is free.

---

## 10. Architectural Invariants (Future Milestones Must Preserve)

| # | Invariant | Rationale |
|---|-----------|-----------|
| 1 | **Capture contract unchanged** — persist-first, queue-second | Guarantees no data loss |
| 2 | **Router switches workspaces only** — no layout/panel/component awareness | Keeps router simple, workspace composition local |
| 3 | **ContextFactory is single context creator** — `AppServices.build_context()` is the only path | No stale context, consistent creation |
| 4 | **Components receive child contexts** — never share root `WorkspaceContext` | Isolation, reusability, testability |
| 5 | **NavigationState remains shell-only** — no project/doc/panel fields | Clean separation; workspace-local nav is additive |
| 6 | **ConversationPanel is a component, never a workspace** | Context-first, not chat-first |
| 7 | **AppServices aggregates all shared services** — no new constructor params without adding to AppServices | Maintains DI cleanliness |
| 8 | **StatusBar owned by shell** — not by views | Persistent across workspace switches |
| 9 | **Navigator selection is silent** — `NavigateRequested` only | No chat pollution, no AI side-effects |
| 10 | **VIEW_REGISTRY contains only WorkspaceView subclasses** | Components stay embeddable, never routable |

---

## File Map (Post-M3.5)

```
src/personal_os/
├── core/
│   ├── capture.py          # Capture contract (persist_capture, capture, capture_and_process)
│   ├── config.py           # get_config, VaultConfig, ModelConfig, …
│   ├── vault.py            # VaultManager, get_vault
│   ├── processor.py        # BackgroundProcessor, process_entry, KnowledgeExtractor/Applier
│   └── search.py           # search, SearchResult
├── tui/
│   ├── app.py              # PersonalOSTUI (shell: NavigatorSidebar + ViewRouter + StatusBar)
│   ├── context.py          # WorkspaceContext, ContextFactory
│   ├── services.py         # AppServices
│   ├── navigation.py       # NavigationState, NAV_ITEMS, NAV_LABELS, DEFAULT_VIEW
│   ├── router.py           # ViewRouter
│   ├── views/
│   │   ├── __init__.py     # VIEW_REGISTRY, WorkspaceView
│   │   ├── base.py         # WorkspaceView contract
│   │   ├── today.py        # TodayView (composed workspace)
│   │   └── placeholders.py # Placeholder views for other NAV_ITEMS
│   ├── components/
│   │   ├── __init__.py
│   │   ├── base.py         # Component base class
│   │   ├── dashboard_panel.py
│   │   ├── conversation_panel.py
│   │   └── markdown_renderer.py (planned)
│   └── widgets/
│       ├── __init__.py
│       ├── navigator.py    # NavigatorSidebar, NavigateRequested
│       └── status_bar.py   # StatusBar
└── cli/main.py             # Typer CLI (pos)
```

---

## Verification Checklist (Run Before Each Milestone Commit)

```bash
# Smoke tests (must pass)
PYTHONPATH=src python -m pytest tests/ -v

# Headless TUI architecture checks
PYTHONPATH=src python -c "
import asyncio
from personal_os.tui.app import PersonalOSTUI
async def test():
    app = PersonalOSTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        router = app.query_one('#view-router')
        assert router.active_id == 'today'
        assert app.nav.active_view == 'today'
        assert app.services.nav is app.nav
        # ContextFactory single path
        ctx = app.services.build_context(app.nav, 'projects')
        assert ctx.workspace_id == 'projects'
        # Child context mechanism
        child = ctx.child(breadcrumbs=['Test'], mode='capture')
        assert child.breadcrumbs == ['Test']
        assert child.workspace_id == 'projects'
        print('✅ All architecture invariants hold')
asyncio.run(test())
"
```

---

## Milestone History

| Milestone | Focus | Key Deliverables |
|-----------|-------|------------------|
| M1 | Capture contract | `capture.py`, persist-first fix |
| M2 | Workspace shell | `NavigationState`, `ViewRouter`, `NavigatorSidebar`, `StatusBar`, `WorkspaceView` contract |
| M3 | Workspace composition | `WorkspaceContext`, `ContextFactory`, `TodayView` with components |
| **M3.5** | **Architecture consolidation** | **Removed legacy panels, ContextFactory ownership, child contexts, shell-only NavigationState, AppServices** |
| M4 | Capture overlay (text) | — |
| M5 | Voice capture | — |
| M6 | Developer trace drawer | — |
| M7 | Knowledge/Favorites/Reviews views | — |

---

**End of Architecture Baseline** — This document is the contract for v0.2. All future work extends from here.