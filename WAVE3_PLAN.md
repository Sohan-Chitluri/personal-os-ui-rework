# Wave 3 Implementation Plan — Personal OS

**Status**: Audit complete. 82 tests passing (Wave 2 verified foundation).

---

## 1. Current Repository State

### Documentation
- `README.md` — project overview, architecture, vault layout
- `ARCHITECTURE_BASELINE.md` — v0.2 reference architecture (M1–M3.5 complete)
- `TUI_DESIGN.md` — design vision (chat-first, sidebar, panels)
- `ROADMAP.md` — Phase 1 complete, Phase 2 (Voice) next
- `CONSTITUTION.md` — principles

### TUI Architecture (v0.2 / M3.5)
```
App (PersonalOSTUI)
├── NavigatorSidebar (persistent, Yazi-style)
├── ViewRouter (center pane, lazy WorkspaceView instantiation)
└── StatusBar (shell-owned, persistent)
```

### Navigation State
- `NavigationState` — shell-only: `active_view`, `history`, `forward_stack`
- `NAV_ITEMS`: today, projects, tasks, journal, knowledge, search, reviews, favorites, settings
- Workspace-local nav (`selected_project`, `breadcrumbs`, `active_document`) reserved for `WorkspaceContext`

### Workspace Composition
- `WorkspaceView` base class with `on_activate`, `on_deactivate`, `refresh_content`
- `VIEW_REGISTRY`: maps view_id → class
- `TodayView` — only composed workspace (DashboardPanel, CapturesPanel, FocusPanel, UpcomingPanel, NotesPanel, TasksPanel in 2-col grid)
- `ProjectsView`, `JournalView`, `SearchView`, etc. — placeholder views

### Data Providers
- `TodayDashboardProvider` — single source of truth for Today dashboard data (caches, refreshes on capture)
- `AllTasksProvider` — reads all Tasks.md across vault, groups by project
- `BigTasksProvider` — reads `## Big Tasks` sections per B1 schema
- Providers receive `AppServices` (vault, config, processor, context_factory, nav)

### Semantic Pipeline (Wave 2 — verified)
- `SemanticExtractor` → `ActionPlanner` → typed `VaultAction` → `KnowledgeApplier`
- `capture_id` (ULID) links raw journal entry to semantic block
- OpenRouter primary, regex fallback
- 11 extraction types, certainty vs confidence distinction

### Vault Structure
- `00-Inbox/`, `02-Projects/<name>/` (8 standard files), `04-Personal/Journal/YYYY-MM-DD.md`
- Atomic writes + git commits per write (current: one commit per file operation)

### Capture Contract (M1)
- `persist_capture()` → `CaptureReceipt` (stage 1: persist, never raises)
- `process_entry()` / `BackgroundProcessor` → stage 2 (async, best-effort)

---

## 2. What's Already Implemented (Wave 3 scope)

| Feature | Status | Notes |
|---------|--------|-------|
| Today workspace | ✅ | Fully composed with 6 panels, real data via TodayDashboardProvider |
| Tasks workspace | ✅ | Full implementation — reads all Tasks.md, groups by project |
| Big Tasks provider | ✅ | Parses `## Big Tasks` schema from Tasks.md |
| Search engine | ✅ | ripgrep + Python fallback, filters (project:, type:, tag:, date:) |
| Vault operations | ✅ | Atomic writes, git, project scaffolding, journal, semantic blocks |
| Capture/processing | ✅ | Persist-first, semantic pipeline, regex fallback |
| Navigation shell | ✅ | NavigatorSidebar, ViewRouter, NavigationState, ContextFactory |
| AppServices DI | ✅ | Clean dependency injection for providers/views |

---

## 3. What Wave 3 Needs

### A. Projects Workspace
**Current**: Placeholder view only.
**Needed**:
- Tree view of all projects with status/health indicators
- Per-project detail: Progress.md, Tasks.md, Notes.md, Decisions.md, Architecture.md, Research.md, Timeline.md, README.md
- Actions: Open project file, Create task, View progress, Archive
- Uses existing `vault.list_projects()` and project file structure
- Can reuse `BigTasksProvider` for big task summary per project

### B. Journal Workspace
**Current**: Placeholder view only.
**Needed**:
- Date navigator (last 30 days, expandable)
- Today expanded by default
- Entry cards showing: timestamp, source, capture preview, status (processed/pending/error)
- Associated semantic block (if any) shown inline as collapsible detail
- Actions: Re-process, Link to project, Convert to task
- Uses `vault.get_journal_entries(date)` and existing semantic block parsing

### C. Search Workspace
**Current**: Search engine exists (`core/search.py`), but no UI.
**Needed**:
- Live search input (debounced ~150ms)
- Filter chips: project:, type:, tag:, date:
- Results grouped by file with context snippets
- Keyboard: Enter=open, p=pin, f=filter menu
- Uses existing `SearchEngine.search(query, limit)` — no new backend needed

### D. T0-B / Batch Vault Commits
**Current**: Every `write_file`/`append_file` commits immediately to git.
**Needed**:
- Transaction-like batching: multiple related vault writes → single commit
- Preserve persist-first safety: journal entry committed immediately on capture
- Processing-stage writes (semantic actions) batched per capture
- Implement in `VaultManager`: `begin_batch()`, `commit_batch(message)`, `rollback_batch()`
- `KnowledgeApplier` uses batch when applying multiple actions from one capture

### E. Integration / Polish
- Wire `refresh_content()` for Projects/Journal/Search views
- Status bar shows processor state correctly per workspace
- Context factory: future workspace-local nav (active_project, breadcrumbs) ready
- Empty states, loading indicators, error handling in all new workspaces

---

## 4. Dependencies Between Tasks

```
                      ┌─────────────────────┐
                      │   T0-B: Batch       │
                      │   Vault Commits     │
                      └──────────┬──────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Projects WS    │    │   Journal WS    │    │  Search WS      │
│  (depends on    │    │  (depends on    │    │  (independent:  │
│   vault API)    │    │   journal API)  │    │   search engine │
└─────────────────┘    └─────────────────┘    │   already done) │
         │                       │             └─────────────────┘
         │                       │                      │
         └───────────────────────┼──────────────────────┘
                                 ▼
                    ┌─────────────────────┐
                    │ Integration / Polish │
                    │ (all workspaces)     │
                    └─────────────────────┘
```

**Key observations**:
- **Search is independent** — search engine already implemented, only UI needed
- **Projects & Journal** depend on existing vault APIs (already stable)
- **Batch commits** is a vault-level change that affects `KnowledgeApplier` and processing pipeline — should be done first or in parallel with careful coordination
- **Integration** comes last

---

## 5. Parallelization Strategy

| Task | Can Parallelize? | Reason |
|------|-----------------|--------|
| Search Workspace UI | ✅ **YES** | Only uses existing `SearchEngine`; no vault writes |
| Projects Workspace UI | ✅ **YES** | Read-only vault APIs; independent of Journal |
| Journal Workspace UI | ✅ **YES** | Read-only vault APIs; independent of Projects |
| Batch Vault Commits | ❌ **NO** | Core vault change; affects `KnowledgeApplier` and all processing |
| Integration/Polish | ❌ **NO** | Depends on all three workspaces + batch commits |

**Recommended parallel workers**: 3 (Search, Projects, Journal) after batch commits is complete or if batch commits is deferred to a separate PR.

---

## 6. Recommended Execution Order

### Phase 1: Foundation (Sequential)
1. **T0-B: Batch Vault Commits** — Modify `VaultManager` and `KnowledgeApplier` to support batched commits for processing-stage writes. Test with existing semantic pipeline.

### Phase 2: Workspace Implementations (Parallel — 3 workers)
2. **Search Workspace** — Implement `SearchView` with live search, filters, grouped results
3. **Projects Workspace** — Implement `ProjectsView` with project tree, detail panels, actions
4. **Journal Workspace** — Implement `JournalView` with date navigator, entry cards, semantic blocks

### Phase 3: Integration (Sequential)
5. **Integration/Polish** — Wire refresh, status bar, context, empty states, keyboard bindings, tests

---

## 7. Expected Files/Modules Affected

### New Files
```
src/personal_os/tui/views/projects.py          # ProjectsView
src/personal_os/tui/views/journal.py           # JournalView
src/personal_os/tui/views/search.py            # SearchView
src/personal_os/tui/components/project_tree.py # Reusable project tree component
src/personal_os/tui/components/journal_entry.py# Reusable journal entry component
src/personal_os/tui/components/search_results.py # Reusable search results component
src/personal_os/tui/providers/projects_provider.py # Projects data provider
src/personal_os/tui/providers/journal_provider.py  # Journal data provider
```

### Modified Files
```
src/personal_os/core/vault.py                  # Batch commit API
src/personal_os/core/processor.py              # KnowledgeApplier uses batch
src/personal_os/tui/views/placeholders.py      # Remove ProjectsView/JournalView/SearchView placeholders
src/personal_os/tui/views/__init__.py          # Export new views
src/personal_os/tui/components/__init__.py     # Export new components
src/personal_os/tui/navigation.py              # NAV_LABELS update ("projects"→"Projects")
src/personal_os/tui/app.py                     # Minor: status bar workspace labels
tests/test_projects_workspace.py               # New tests
tests/test_journal_workspace.py                # New tests
tests/test_search_workspace.py                 # New tests
tests/test_batch_commits.py                    # New tests
```

---

## 8. Tests Required

| Test File | Coverage |
|-----------|----------|
| `test_batch_commits.py` | VaultManager batch API, KnowledgeApplier batched writes, rollback, persist-first guarantee |
| `test_projects_workspace.py` | ProjectsView renders project list, detail shows correct files, actions work |
| `test_journal_workspace.py` | JournalView shows dates/entries, semantic blocks expand, actions work |
| `test_search_workspace.py` | SearchView live search, filters, results grouping, keyboard nav |
| Existing tests | All 82 Wave 2 tests must continue passing |

---

## 9. Risks & Architectural Ambiguities

| Risk | Mitigation |
|------|------------|
| Batch commits could break persist-first if journal writes are batched | **Only batch processing-stage writes**; journal `append_journal_entry` stays immediate commit |
| Search workspace performance without tantivy index | ripgrep is fast enough for vault sizes <10k files; Python fallback for systems without rg |
| Project detail view complexity (8 files) | Start with read-only tabs/panels; editing comes in later milestone |
| Journal semantic blocks may be large | Render as collapsible YAML; only show on demand |
| Keyboard binding conflicts across workspaces | Use `WorkspaceContext.mode` to scope bindings; global bindings stay in App |
| Context factory future-proofing | Reserve `active_project`, `active_document`, `breadcrumbs` in `WorkspaceContext` (already documented in ARCHITECTURE_BASELINE.md) |

---

## 10. Acceptance Criteria (Wave 3 Complete)

- [ ] `python -m pytest tests/ -q` → **all 82+ new tests pass**
- [ ] Projects workspace: navigate to projects, see all projects with status, open project detail, see all 8 files
- [ ] Journal workspace: navigate to journal, see date list, expand today, see captures with semantic blocks
- [ ] Search workspace: type query, see live results, filter by project/type/tag/date, open result
- [ ] Batch commits: single capture → multiple vault writes → **single git commit** for processing stage; journal entry still commits immediately
- [ ] No regression in Wave 2 semantic pipeline
- [ ] No architectural invariants violated (ARCHITECTURE_BASELINE.md checklist passes)

---

## 11. Next Steps

1. **Start with T0-B (Batch Vault Commits)** — core change, enables cleaner processing
2. **Then parallelize the three workspace implementations**
3. **Integration last**

Ready to begin implementation.