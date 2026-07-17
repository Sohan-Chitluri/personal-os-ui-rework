# Active Tasks

> Canonical implementation: Python package `src/personal_os` (CLI `pos`, Textual
> TUI). Legacy `~/.hermes/skills/*` are deprecated/superseded and retained
> out-of-repo. See `PARITY.md` for the legacy→canonical mapping.

## Phase 1: Core Personal OS ✅ COMPLETE (v0.1.0)

### Core Infrastructure (canonical package)
- [x] `src/personal_os` package as single implementation
- [x] Markdown vault as source of truth (`VaultManager`)
- [x] `pos` CLI (Typer) + Textual TUI
- [x] Core engines: config, vault, processor, search, links, weekly_review
- [x] Legacy in-repo code removed (`Scripts/`, `skill_registry.json` — commit #8)

### Capabilities
- [x] **note** — natural-language capture + auto-process
- [x] **today** — show today's journal
- [x] **search** — vault-wide search, `project:`/`type:`/`tag:`/`date:` filters
- [x] **new-project** — 8-file scaffolding from canonical template
- [x] **projects** / **tasks** — listing and status views
- [x] **links** — suggest / related / orphans / hub knowledge-graph ops
- [x] **weekly-review** — collect → analyze → render, deterministic per week
- [x] **process** — reprocess journal entries
- [x] **init** / **config** — isolated vault init + config persistence

### Quality
- [x] Smoke-test suite under `tests/` (isolated temp vault)
- [x] Feature parity report (`PARITY.md`)
- [x] Documentation updated to canonical architecture

---

## Phase 2: Voice & Natural Interaction 🎯 NEXT (after v0.1.0)

### Voice Integration
- [ ] Wire `pos voice` to whisper.cpp (local transcription)
- [ ] Push-to-talk capture script
- [ ] Continuous background capture
- [ ] Voice summaries (daily/weekly)
- [ ] Hands-free interaction mode

### Natural Interaction
- [ ] Single "talk to Hermes" interface (no commands)
- [ ] Intent recognition from natural language
- [ ] Automatic capability selection (note vs update vs search vs create)
- [ ] Context-aware responses

---

## Phase 3: Intelligence
- [ ] Daily brief (`pos daily-brief`) exposure
- [ ] Weekly review automation + dashboards
- [ ] Knowledge consolidation (stream → projects)
- [ ] Suggestions (proactive, pattern-based)
- [ ] Smart prioritization (deadlines, energy, context)
- [ ] Blocker detection and alerts

## Phase 4: Automation & Integrations (Ongoing)
- [ ] Calendar integration (read-only)
- [ ] GitHub integration (issues, PRs)
- [ ] External tools (Vivado, Git, academic portals)
- [ ] Optional automation rules
- [ ] Multi-device improvements

---

## Inbox (to be processed)
- [ ] Migrate `Templates/daily-template.md` / `meeting-template.md` from
      Obsidian-Templater syntax to canonical `{{var}}` syntax
- [ ] Decide on `pos daily-brief` command shape (ReviewData already supports it)

## Waiting For
- [ ] Audio stack install/test on NixOS (whisper.cpp)
- [ ] Decision on voice capture UX (push-to-talk vs continuous)

## Projects Overview
- [[02-Projects/personal-os]] - Personal Operating System (this project)
- [[01-Academic/Current-Semester]] - Academic coursework
- [[02-Projects/Team-NAMO]] - Team NAMO projects and commitments
