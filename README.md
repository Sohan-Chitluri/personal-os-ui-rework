# Personal OS

A terminal-native, Markdown-vault personal operating system.

> **AI-first · Conversation-first · Local-first · Markdown-first · Editor-agnostic**

`src/personal_os` is the single canonical implementation. There is no legacy
skill layer or script directory in this repository.

## Overview

Personal OS manages a local Markdown vault through a conversational CLI (`pos`)
and a Textual TUI. It captures notes, extracts structured knowledge, searches the
vault, links notes into a knowledge graph, and generates weekly reviews — all
backed by plain Markdown files under Git.

Design invariants:

- **No hardcoded vault paths** — every path resolves from `VaultConfig`.
- **No surprise git commits** — vault writes are local-file only by design.
- **Never rewrites existing vault content** automatically.

## Architecture

```
User
  └─ pos (Typer CLI)  /  TUI (Textual)
        └─ core/   (reusable, editor-agnostic engines)
             ├─ config         Config / VaultConfig  (PERSONAL_OS_VAULT overrides)
             ├─ vault          VaultManager (atomic writes, git tracking, scaffolding)
             ├─ processor      KnowledgeExtractor / KnowledgeApplier  (process_entry)
             ├─ search         SearchEngine (ripgrep + Python fallback)
             ├─ links          LinkGraph (suggest / related / orphans / hub)
             └─ weekly_review  WeeklyReviewGenerator (collect → analyze → render)
        └─ skills/voice_capture   record + transcribe (whisper.cpp, optional)
              └─ Markdown Vault (source of truth) + Git
```

## Install

```bash
pip install -e .                 # editable install of the `pos` command
pip install -e . --no-deps --no-build-isolation   # if offline / pinned
```

The `pos` command maps to `personal_os.cli.main:app`.

## Usage

```bash
pos                                       # launch the TUI
pos note "Spent 2h on Maestro fixing router"   # quick capture + auto-process
pos today                                 # show today's journal
pos search "router" --project Maestro     # vault-wide search
pos projects                              # list projects
pos tasks --status open                   # list open tasks
pos new-project MyThing --description "…"  # scaffold an 8-file project
pos links suggest "Maestro"               # knowledge-graph suggestions
pos links orphans                         # notes with no inbound/outbound links
pos weekly-review                         # generate this week's review
pos init --path ~/vaults/x                # initialize a vault elsewhere
pos config --show                         # show effective config
```

Run `pos --help` for the full command list.

## Vault Layout

```
00-Inbox/   01-Academic/   02-Projects/   03-Skills/
04-Personal/Journal/   05-Resources/   06-Archive/   Templates/
```

Each project gets the standard 8 files: `README`, `Architecture`, `Notes`,
`Tasks`, `Progress`, `Timeline`, `Decisions`, `Research`.

## Templates

- `Templates/project-template.md` — the single canonical source, using
  editor-agnostic `{{var}}` syntax. Rendered by `vault.create_project`.
- `Templates/daily-template.md` / `meeting-template.md` — retained as authoring
  references but still use Obsidian-Templater syntax and are **not** yet consumed
  by application code (TODO: migrate to `{{var}}` when integrated).

## About `~/.hermes`

The Hermes home profile (`~/.hermes/skills/*`) is **intentionally outside this
repository**. Its legacy skills (`daily_stream`, `update_project`,
`create_project`, `search_vault`, `link_notes`, `weekly_review`,
`voice_capture`) are **deprecated and superseded** by `src/personal_os` but are
**retained** as-is (out of repository scope). Do not depend on them.

## Feature Parity & Limitations

See `PARITY.md` for the legacy → canonical mapping and the list of remaining
gaps (e.g. `pos daily-brief` not yet exposed; daily/meeting template migration
pending).

## Development

```bash
python -m pytest tests/     # lightweight smoke tests (isolated temp vault)
```

See `ROADMAP.md` for the roadmap and `CONSTITUTION.md` for principles.
