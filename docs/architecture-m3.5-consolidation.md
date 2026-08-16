# Personal OS v0.2 — Architecture Consolidation (Milestone 3.5)

> **Status**: Complete (post-M3.5)  
> **Scope**: Architectural cleanup only — no user-facing features added  
> **Verification**: All 8 smoke tests pass; headless TUI tests pass

---

## Executive Summary

Milestone 3.5 performed targeted architectural consolidation on the v0.2 workspace shell. Five objectives were delivered in a single reviewable commit, strengthening the foundation for future workspace-local navigation and component composition without changing any user-visible behavior.

| Objective | Status | Key Artifact |
|-----------|--------|--------------|
| 1. Remove legacy widget tree | ✅ | `widgets/panels/` hierarchy deleted |
| 2. WorkspaceContext ownership | ✅ | `ContextFactory` — single creator |
| 3. Child contexts for components | ✅ | `WorkspaceContext.child()` in `TodayView` |
| 4. Navigation scope separation | ✅ | `NavigationState` = shell-only |
| 5. Service aggregation | ✅ | `AppServices` dataclass |

---

## 1. Legacy Widget Tree — Removed

**Before**: `src/personal_os/tui/widgets/panels/` contained 7 files (`base.py`, `projects.py`, `tasks.py`, `journal.py`, `search.py`, `calendar.py`, `settings.py`) — the old 6-panel chat shell.

**After**: Directory deleted. No imports reference it. The new component architecture (`tui/components/`) is now the **only** supported UI architecture.

**Verification**:
```bash
$ find src -name "panels" -type d
# (no output)
$ grep -r "widgets.panels" src/
# (no output)
```

---

## 2. WorkspaceContext Ownership — ContextFactory

### Single Authoritative Creator

```
App (owns ContextFactory)
    └─> ContextFactory.build(nav_state)
            └─> WorkspaceContext(workspace_id=nav_state.active_view, ...)
```

**Location**: `src/personal_os/tui/context.py`

```python
class ContextFactory:
    """Single authoritative factory for building WorkspaceContext instances."""
    
    def __init__(self, vault, config):
        self.vault = vault
        self.config = config

    def build(self, nav_state) -> WorkspaceContext:
        return WorkspaceContext(
            workspace_id=nav_state.active_view,
            mode="explore",
            vault=self.vault,
            config=self.config,
            # Future workspace-local fields (reserved):
            # active_project=nav_state.selected_project,
            # active_document=nav_state.selected_document,
            # breadcrumbs=nav_state.breadcrumbs,
        )
```

### Integration Points

- **App** owns one `ContextFactory` (created in `__init__`)
- **AppServices** exposes `build_context(nav_state, workspace_id)` — the **single path** for context creation
- **ViewRouter.show()** calls `services.build_context()` when switching views
- **Context is refreshed on every workspace switch** — no stale context possible

---

## 3. Child Contexts — Component Scoping

### Standard Mechanism

```python
# In TodayView.compose()
root_ctx = self.context  # Set by router via AppServices

dashboard_ctx = root_ctx.child(
    breadcrumbs=["Today", "Dashboard"],
    mode="explore",
)
yield _Region("Dashboard", DashboardPanel(context=dashboard_ctx))

project_ctx = root_ctx.child(
    breadcrumbs=["Today", "Current Project"],
    mode="explore",
)
yield _Region("Current Project", ConversationPanel(context=project_ctx))
```

### Contract

> **Components should NEVER share the root WorkspaceContext instance directly. Use `child()` to create a scoped context for each component.**

**Why**: Isolates breadcrumbs, mode, and future workspace-local navigation per component. Keeps components reusable and testable in isolation.

---

## 4. Navigation Scope Separation

### NavigationState — Shell Only

```python
@dataclass
class NavigationState:
    """Owns the app's SHELL navigation. One instance lives on the App."""
    
    active_view: str = DEFAULT_VIEW      # current workspace id
    history: List[str] = field(default_factory=list)  # back stack
    forward_stack: List[str] = field(default_factory=list)  # forward stack
    
    # WORKSPACE-LOCAL FIELDS ARE INTENTIONALLY NOT HERE:
    # selected_project, selected_document, focused_panel, breadcrumbs
    # → These belong in WorkspaceContext (future milestone)
```

### What This Enables

| Navigation Level | Owned By | Current Milestone |
|------------------|----------|-------------------|
| Shell (workspace switching, back/forward) | `NavigationState` | ✅ Implemented |
| Workspace-local (project, document, panel focus) | `WorkspaceContext` | 🔜 Future milestone |

**Key invariant**: Adding workspace-local navigation later requires **zero refactor** of shell navigation — just populate the reserved fields in `ContextFactory.build()`.

---

## 5. Service Aggregation — AppServices

### Problem
Constructors were growing: `vault`, `config`, `processor`, `context_factory`, `nav` — 5 parameters passed through multiple layers.

### Solution
```python
@dataclass
class AppServices:
    vault: Any
    config: Any
    processor: Any
    context_factory: ContextFactory
    nav: NavigationState

    @classmethod
    def from_app(cls, vault, config, processor, context_factory, nav):
        return cls(vault=vault, config=config, processor=processor,
                   context_factory=context_factory, nav=nav)

    def build_context(self, nav_state, workspace_id):
        # Single authoritative path for context creation
```

### Consumers
- `PersonalOSTUI` — creates one instance in `__init__`
- `ViewRouter` — receives via `__init__(services: AppServices)`
- `WorkspaceView` — receives via `__init__(services: AppServices)`
- Tests — can inject a mock `AppServices` for isolation

---

## Architecture Diagram (Post-M3.5)

```
PersonalOSTUI (App)
├── NavigationState          ← Shell navigation (active_view, history, forward)
├── ContextFactory           ← Single WorkspaceContext creator
├── AppServices              ← Aggregates: vault, config, processor, factory, nav
│
├── NavigatorSidebar         ← Silent selection → NavigateRequested
│   └── (consumes NavigationState)
│
├── ViewRouter               ← Hosts ONE WorkspaceView
│   ├── services: AppServices
│   └── show(view_id):
│       1. Deactivate previous view
│       2. view.context = services.build_context(nav, view_id)
│       3. Mount + on_activate()
│
├── WorkspaceView (TodayView)
│   ├── services: AppServices
│   ├── context: WorkspaceContext (set by router)
│   └── compose():
│       root_ctx = self.context
│       for each region:
│           child_ctx = root_ctx.child(breadcrumbs=[...], mode=...)
│           yield Component(context=child_ctx)
│
└── StatusBar                ← Shell-owned (mode, workspace, processor status)
```

---

## Component Hierarchy (Canonical)

```
Workspace (TodayView, ProjectsView, ...)
    → Layout (vertical/horizontal regions)
        → Panel (_Region with label)
            → Component (DashboardPanel, ConversationPanel, MarkdownRenderer, ...)
                ↓ receives: WorkspaceContext via .child()
```

**Rules**:
- Router switches **Workspaces only** — knows nothing about Layout/Panel/Component
- Components live in `tui/components/` — **never** in `VIEW_REGISTRY`
- Conversation is a **component** (`ConversationPanel`), **not** a workspace
- `today.py` is the reference implementation for composed workspaces

---

## Capture Contract (Unchanged)

```python
# src/personal_os/core/capture.py

# STAGE 1: Persist raw (guaranteed, atomic, git-committed)
receipt = persist_capture(text, source="Text", vault=vault)
assert receipt.persisted  # Raw capture is SAFE

# STAGE 2: Process (best-effort, async)
result = await process_entry(text, "Text")
```

**Milestone 3.5 did not touch this** — it remains the immutable foundation.

---

## Verification Checklist

| Check | Command | Expected |
|-------|---------|----------|
| No orphaned panels | `find src -name "panels" -type d` | (empty) |
| No panel imports | `grep -r "widgets.panels" src/` | (empty) |
| ContextFactory single creator | `grep -r "ContextFactory" src/` | Only in `context.py`, `app.py`, `services.py` |
| Child contexts used | `grep "root_ctx.child" src/personal_os/tui/views/today.py` | 4 occurrences |
| NavigationState shell-only | `grep -A20 "class NavigationState" src/personal_os/tui/navigation.py` | Only active_view, history, forward_stack |
| AppServices aggregates 5 services | `grep -A10 "class AppServices" src/personal_os/tui/services.py` | vault, config, processor, context_factory, nav |
| Smoke tests pass | `PYTHONPATH=src pytest tests/ -v` | 8 passed |
| Headless TUI test | `python -c "..."` (see below) | All assertions pass |

### Headless TUI Verification Script

```python
import asyncio
from personal_os.tui.app import PersonalOSTUI

async def test():
    app = PersonalOSTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        
        router = app.query_one("#view-router")
        assert router.active_id == "today"
        assert app.nav.active_view == "today"
        
        # Navigate via number key
        await pilot.press("2")  # projects
        await pilot.pause()
        assert router.active_id == "projects"
        assert app.nav.active_view == "projects"
        
        # Back navigation
        app.nav.back()
        app._apply_active_view()
        await pilot.pause()
        assert router.active_id == "today"
        assert app.nav.active_view == "today"
        
        # TodayView has context with child contexts
        today_view = router._views["today"]
        assert today_view.context.workspace_id == "today"
        child = today_view.context.child(breadcrumbs=["Today", "Dashboard"])
        assert child.breadcrumbs == ["Today", "Dashboard"]
        
        # AppServices aggregation
        assert all([app.services.vault, app.services.config, 
                    app.services.processor, app.services.context_factory,
                    app.services.nav])
        
        print("✅ All M3.5 architectural checks passed")

asyncio.run(test())
```

---

## What Was NOT Done (Intentional Deferral)

| Feature | Milestone |
|---------|-----------|
| Markdown rendering | M4+ |
| Dashboard data binding | M4+ |
| Voice capture overlay | M5 |
| Workspace-local navigation (project/doc/panel selection) | M4 |
| Search implementation | M4 |
| Split panes / themes / animations | M6+ |
| Conversation redesign (beyond embeddable panel) | M5+ |

---

## Files Modified in M3.5

| File | Change |
|------|--------|
| `src/personal_os/tui/app.py` | Added `nav=self.nav` to `AppServices.from_app()`; updated docstring |
| `src/personal_os/tui/context.py` | Added docstring clarifying single-authority + child() contract |
| `src/personal_os/tui/services.py` | Added `nav` field + `build_context()` method |
| `src/personal_os/tui/router.py` | Uses `services.build_context()` in `show()` |
| `src/personal_os/tui/views/base.py` | Receives `services` + `context` from router |
| `src/personal_os/tui/views/today.py` | Uses `context.child()` for each component region |
| `src/personal_os/tui/navigation.py` | Docstring emphasizes shell-only scope |
| `src/personal_os/tui/widgets/navigator.py` | No change (already silent) |
| `src/personal_os/tui/widgets/status_bar.py` | No change (already shell-owned) |
| **Deleted** | `src/personal_os/tui/widgets/panels/` (7 files) |

---

## Next Milestone: Workspace-Local Navigation (M4)

With M3.5 complete, the architecture is prepared for:

1. **Add reserved fields to `NavigationState`** (or a new `WorkspaceNavigationState`):
   ```python
   selected_project: Optional[str] = None
   selected_document: Optional[Path] = None
   focused_panel: Optional[str] = None
   breadcrumbs: List[str] = field(default_factory=list)
   ```

2. **Wire them through `ContextFactory.build()`** — zero shell changes

3. **Expose workspace-local actions** (project select, document open, panel focus) via messages that update `WorkspaceContext` — not `NavigationState`

4. **Persist workspace-local state** per workspace (so switching back restores project/doc/focus)

The shell/workspace boundary remains clean; all new code is additive.