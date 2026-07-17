# Personal OS — Release Notes v0.1.0

> Release date: 2026-07-17
> Tag: `v0.1.0`
> Status: Migration complete. Canonical implementation: `src/personal_os`.

## Summary

v0.1.0 is the first tagged release of the consolidated Personal OS. The
repository now contains a **single canonical implementation** — the Python
package `src/personal_os` — with no in-repository legacy code. All previously
scattered skills, scripts, and duplicate engines have been unified into a
small set of reusable, editor-agnostic core modules.

## Major Architectural Changes

- **One canonical package.** `src/personal_os` replaces the legacy
  `~/.hermes/skills/*` layers and the in-repo `Scripts/` CLI. There is no
  `Scripts/` directory and no `skill_registry.json` in the repository.
- **Reusable core engines** (not CLI-coupled): every capability is exposed as
  an importable service that the CLI, TUI, and future AI components share.
- **No hardcoded vault paths.** All paths resolve from `VaultConfig`; the
  vault root defaults to `~/projects/personal-os` and is overridable via
  `PERSONAL_OS_VAULT` or `pos init --path`.
- **No surprise git commits.** Legacy auto-committed the whole vault
  (`git add -A && commit`). The package performs local file writes only by
  design (the user flagged surprise commits as a bug).
- **Editor-agnostic templates.** The single canonical template is
  `Templates/project-template.md` using `{{var}}` syntax. Obsidian-Templater
  is no longer the canonical system.
- **`~/.hermes` is external and deprecated.** The Hermes home profile's
  legacy skills are retained out-of-repo but superseded; do not build on them.

## Migrated Components

| Concern | Legacy location | Canonical location |
|--------|----------------|--------------------|
| CLI entry | `Scripts/personal-os` (argparse) | `src/personal_os/cli/main.py` (`pos`, Typer) |
| Vault I/O | per-skill scripts | `core/vault.py` (`VaultManager`) |
| Config | env + per-skill defaults | `core/config.py` (`VaultConfig`, `PERSONAL_OS_*`) |
| Processing | `daily_stream` processor | `core/processor.py` (`process_entry`) |
| Search | `search_vault` + `skills/search.py` | `core/search.py` (ripgrep + Python fallback) |
| Note linking | `link_notes` | `core/links.py` (`LinkGraph`) |
| Weekly review | `weekly_review` | `core/weekly_review.py` (`WeeklyReviewGenerator`) |
| Voice capture | `voice_capture` | `skills/voice_capture.py` |
| Project creation | `create_project` | `core/vault.py::create_project` |
| Skill registry | `Scripts/skill_registry.json` | removed (no registry in new arch) |

### CLI surface (`pos`)
`note`, `voice`, `today`, `search`, `projects`, `tasks`, `new-project`,
`open-project`, `links` (`suggest`/`related`/`orphans`/`hub`),
`weekly-review` (+ `review` alias), `process`, `init`, `config`.
Running `pos` with no arguments launches the Textual TUI.

## Bug Fixed in Finalization
- `core/vault.py::_init_git` — a freshly initialized (`git init`) repo raised
  inside `iter_commits()` before the vault structure was created, leaving new
  vaults empty and broken. Fixed so a fresh `pos init` always builds the
  standard directory layout and initial commit. Covered by
  `tests/test_smoke.py::test_vault_init_creates_structure`.

## Known Limitations (not regressions)

1. **Daily Brief** (`pos daily-brief`) not yet exposed — `ReviewData` already
   supports it; enhancement only.
2. **Daily/meeting templates** (`Templates/daily-template.md`,
   `meeting-template.md`) still use Obsidian-Templater syntax and are not
   consumed by application code (only `project-template.md` is canonical
   `{{var}}`).
3. **Voice capture** (`pos voice`) depends on external **whisper.cpp**; degrades
   gracefully when absent (same constraint as legacy).
4. **Search** is ripgrep + Python fallback — no semantic/embeddings search yet.
5. **`~/.hermes` legacy skills** are deprecated and redundant but retained
   out-of-repository.

## Test & Verification
- `tests/` smoke suite: 8 scenarios (CLI startup, vault init, project
  creation, note capture, processing, search, links, weekly review). All pass
  in an isolated temp vault; the real user vault is never touched.
- Final release gate (all green): working tree clean, imports OK
  (`__version__ == 0.1.0`), CLI operational, docs present, legacy removed.

## Roadmap Highlights

### v0.2 — Voice-First Workflow (next phase)
The exclusive focus after release: wire `pos voice` to whisper.cpp, push-to-talk
hotkey, continuous background capture, voice activity detection, voice summaries,
and hands-free "Hey Hermes, …" capture/query. Improve day-to-day UX through
real-world usage rather than expanding infrastructure. Also: migrate
daily/meeting templates to `{{var}}` syntax.

### Later (v0.3+)
- `pos daily-brief` exposure (automated evening summary + next-day MITs)
- Semantic / embeddings search
- Smart prioritization (deadline / energy / context aware)
- Calendar & GitHub integrations (read-only)

## Install
```bash
pip install -e .          # provides the `pos` command
pos                       # launch TUI
pos --help                # command reference
```
