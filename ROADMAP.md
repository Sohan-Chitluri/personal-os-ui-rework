# Personal OS Roadmap

> Canonical implementation: the Python package `src/personal_os` (CLI `pos`,
> Textual TUI). The legacy `~/.hermes/skills/*` layer is deprecated and
> superseded; it is retained outside the repo and should not be built upon.

## Current Release: v0.1.0

The core package is feature-complete and canonical. Legacy in-repo code
(`Scripts/`, `skill_registry.json`) was removed in commit #8. Feature parity
with the legacy implementation is documented in `PARITY.md`.

---

## Phase 1 — Core Personal OS ✅ COMPLETE (as `src/personal_os`)

### Capture
- [x] Raw entry storage to `04-Personal/Journal/YYYY-MM-DD.md`
- [x] Terminal capture command (`pos note "..."`)
- [x] Today view (`pos today`)
- [x] Auto-processing pipeline (`core.processor.process_entry`)
- [x] Project progress extraction from stream
- [x] Decision logging with rationale
- [x] Task completion detection
- [x] Learning progress tracking
- [ ] Voice capture with whisper.cpp → **see Phase 2**

### Project Management
- [x] Project scaffolding (`vault.create_project`, `pos new-project`)
- [x] Standard 8-file structure (README, Architecture, Notes, Tasks, Progress, Timeline, Decisions, Research)
- [x] Project progress logging (`Progress.md`)
- [x] Task management (create/update/complete across projects)
- [ ] Smart task prioritization
- [ ] Cross-project task views

### Search & Recall
- [x] Canonical vault search (`core.search`, ripgrep + Python fallback)
- [x] Filters: `project:`, `type:`, `tag:`, `date:`
- [x] Context snippets with line numbers
- [ ] Semantic search (embeddings)
- [ ] Natural-language "show me everything about X"

### Knowledge Graph
- [x] Link suggestions (`pos links suggest`)
- [x] Related-notes discovery (`pos links related`)
- [x] Orphan detection (`pos links orphans`)
- [x] Hub creation (`pos links hub`)
- [x] Entity extraction (wiki-links, tags, projects, tech keywords)

### Weekly Review (canonical `core.weekly_review`)
- [x] Collect → analyze → render pipeline
- [x] Metrics dashboard (hours, decisions, blockers, tasks)
- [x] Deterministic per-ISO-week output (no duplicates)
- [ ] Daily brief exposure (`pos daily-brief`) — enhancement, not regression
- [ ] Monthly retrospective

### Vault & Config
- [x] Isolated vault init (`pos init --path`)
- [x] Config persistence (`~/.config/personal-os/config.toml`)
- [x] `PERSONAL_OS_VAULT` env override for isolation
- [x] Atomic file writes + git tracking (no surprise commits)

---

## Phase 2 — Voice & Natural Interaction (NEXT)

> **This is the next development phase after v0.1.0.**

### Voice Integration (Primary Input)
- [ ] Whisper.cpp integration (local, fast) wired into `pos voice`
- [ ] Push-to-talk hotkey
- [ ] Continuous background capture
- [ ] Voice activity detection
- [ ] Offline transcription
- [ ] Speaker identification (optional)

### Continuous Daily Stream
- [ ] Background capture daemon
- [ ] Automatic silence detection
- [ ] Segment transcription
- [ ] Real-time stream display
- [ ] "What did I say today?" voice query

### Voice Summaries
- [ ] "Summarize today" → spoken summary
- [ ] "What did I work on?" → voice response
- [ ] "What's blocking me?" → voice response
- [ ] Morning briefing on wake

### Hands-Free Interaction
- [ ] "Hey Hermes, capture…" hotword
- [ ] "Hey Hermes, what's my next task?" → spoken answer
- [ ] "Hey Hermes, remind me to…" → creates task
- [ ] "Hey Hermes, search for…" → voice results

---

## Phase 3 — Intelligence
- [ ] Daily review (automated evening summary + tomorrow's MITs)
- [ ] Knowledge consolidation (stream → projects)
- [ ] Observation → decision pipeline
- [ ] Duplicate detection and merging
- [ ] Suggestions & pattern recognition
- [ ] Smart prioritization (deadline/energy/context aware)

---

## Phase 4 — Automation & Integrations (Ongoing)
- [ ] Calendar integration (read-only)
- [ ] GitHub integration (issues, PRs, release notes)
- [ ] External tool parsing (Vivado/Vitis, PCB fab, academic portals)
- [ ] Optional automation rules
- [ ] Multi-device sync (Syncthing/Nextcloud)

---

## Success Metrics

### Phase 1 (met by v0.1.0)
- [x] Single canonical implementation (`src/personal_os`)
- [x] Search finds anything across the vault
- [x] Projects stay organized without manual filing
- [x] No surprise git commits to the vault
- [x] Feature parity with legacy implementation

### Phase 2
- [ ] Voice capture is primary input method
- [ ] Hands-free operation for 80% of captures
- [ ] Transcription accuracy >95%
- [ ] Latency <2s from speech to stored entry

*Last updated: 2026-07-17 — v0.1.0 release prep*
