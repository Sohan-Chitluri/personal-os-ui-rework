# Session Knowledge Base — Personal OS (canonical architecture)

> **Purpose**: Cross-session knowledge of how Personal OS actually works now.
> This describes the **canonical `src/personal_os` package** — not the legacy
> skill layer.

---

## What Personal OS Is Now

A terminal-native, Markdown-vault personal OS. The single implementation is the
Python package `src/personal_os` (CLI `pos` via Typer, plus a Textual TUI).
There is **no `Scripts/` directory, no `skill_registry.json`, and no in-repo
legacy code**. All capabilities run through the canonical package.

Principles: AI-first · Conversation-first · Local-first · Markdown-first ·
Editor-agnostic.

---

## Repository Layout (canonical)

```
personal-os/
├── src/personal_os/
│   ├── __init__.py            # __version__ = "0.1.0"
│   ├── cli/main.py            # Typer `pos` app (entry: personal_os.cli.main:app)
│   ├── core/
│   │   ├── config.py          # Config, VaultConfig, get_config()
│   │   ├── vault.py           # VaultManager, get_vault()
│   │   ├── processor.py       # KnowledgeExtractor / KnowledgeApplier / process_entry()
│   │   ├── search.py          # SearchEngine + search()  (ripgrep + python fallback)
│   │   ├── links.py           # LinkGraph + suggest_*/find_*/create_hub()
│   │   └── weekly_review.py   # WeeklyReviewGenerator + generate_review()
│   ├── skills/voice_capture.py
│   └── tui/                   # Textual TUI application
├── Templates/                 # project-template.md (canonical) + daily/meeting (TODO)
├── PARITY.md                  # legacy → canonical mapping + gaps
├── ROADMAP.md  TASKS.md  CONSTITUTION.md
└── (vault dirs) 00-Inbox … 06-Archive
```

---

## CLI: `pos`

| Command | Purpose |
|---------|---------|
| `pos` (no args) | Launch the Textual TUI |
| `pos note "<text>"` | Capture a note + auto-process |
| `pos today` | Show today's journal |
| `pos search "<q>" [--project] [--type]` | Vault search (ripgrep + fallback) |
| `pos projects [--status]` | List projects |
| `pos tasks [--project] [--status open\|done]` | List tasks |
| `pos new-project <name> [--description] [--phase]` | Scaffold 8-file project |
| `pos open-project <name>` | Show project file map |
| `pos links suggest\|related\|orphans\|hub "<q>"` | Knowledge-graph ops |
| `pos weekly-review` (`pos review`) | Generate this week's review |
| `pos process [--today/--all] [--dry-run]` | Re-process journal |
| `pos init [--path]` | Initialize a vault |
| `pos config [--show] [--edit]` | Show/edit config |
| `pos voice [--duration] [--model]` | Voice capture (whisper.cpp) |
| `pos --version` | Print `Personal OS v0.1.0` |

---

## Core Engines (reusable, editor-agnostic)

- **config** — `Config`/`VaultConfig`. Vault root defaults to
  `~/projects/personal-os`; override with `PERSONAL_OS_VAULT`. Config persists to
  `~/.config/personal-os/config.toml`.
- **vault** — `VaultManager`: atomic writes, git init/commit of vault files,
  journal append, `create_project` (renders `Templates/project-template.md`),
  project/task/progress readers. No surprise commits beyond explicit vault ops.
- **processor** — `process_entry(text, source)` extracts
  progress/decision/completion/learning/deadline/observation/task and applies
  them to the right vault files. Project matching uses an alias map
  (maestro→Maestro, vlsi→VLSI, …).
- **search** — `search(query, limit)` parses `project:`/`type:`/`tag:`/`date:`
  filters; uses `rg` when present, else pure Python over `*.md`.
- **links** — `LinkGraph` discovers entities (wiki-links, tags, projects, tech
  keywords); `suggest_links`, `find_related`, `find_orphans` are read-only;
  `create_hub` writes a hub note under `05-Resources/Hubs/`.
- **weekly_review** — `WeeklyReviewGenerator` collects (journal, progress,
  tasks, learning) → analyzes into `ReviewData` → renders Markdown. Output is
  deterministic per ISO week, so re-runs overwrite, never duplicate.

---

## Vault Layout

```
00-Inbox/  01-Academic/  02-Projects/  03-Skills/
04-Personal/Journal/  05-Resources/  06-Archive/  Templates/
```

Each project: `README, Architecture, Notes, Tasks, Progress, Timeline,
Decisions, Research`.

---

## Voice Integration (Phase 2 — Next Priority)

- `pos voice` delegates to `src/personal_os/skills/voice_capture.py`.
- Depends on external **whisper.cpp** (`/usr/bin/whisper-cli` on NixOS).
- Degrades gracefully (logs error, returns) when whisper.cpp or the model is
  absent — same constraint as the legacy layer, not a regression.
- Config in `core/config.py::VoiceConfig` (engine, model, sample_rate, …).

---

## About `~/.hermes`

The Hermes home profile (`~/.hermes/skills/*`) is **intentionally outside this
repository**. Its legacy skills (`daily_stream`, `update_project`,
`create_project`, `search_vault`, `link_notes`, `weekly_review`,
`voice_capture`) are **deprecated and superseded** by `src/personal_os` but are
**retained** as-is. They are redundant; do not build on them.

---

## Current Status

- Core package complete and canonical (commits #1–#8; legacy removed in #8).
- Target release: **v0.1.0**.
- Next phase: voice capture + complete voice-first workflow (Phase 2).

---

## User & AI Identity

- **User**: Sohan — Electronics Engineering student (VIT Chennai, 2024–2028);
  Electronics Lead, Team NAMO; focus VLSI / Embedded / Robotics / Linux / FPGA.
- **AI**: Morales — AI partner / chief of staff. Spider-Man legacy: responsible
  heroism via technical excellence and helpful partnership.

---

*Canonical architecture snapshot for v0.1.0. Cross-session continuity lives in
the vault itself, not in this file's legacy framing.*
