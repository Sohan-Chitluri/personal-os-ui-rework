# Personal OS — Feature Parity Report

Status: NEW package (`src/personal_os`) is canonical.
Purpose: document coverage of legacy functionality and remaining gaps.

## Architecture summary

| Concern | Legacy location | New canonical location |
|--------|----------------|------------------------|
| CLI entry | `Scripts/personal-os` (argparse) + `~/.hermes/skills/*` | `src/personal_os/cli/main.py` (Typer `pos`) |
| Vault I/O | `~/.hermes/skills/*/...py` per-skill | `src/personal_os/core/vault.py` (VaultManager) |
| Config | env + per-skill defaults | `src/personal_os/core/config.py` (VaultConfig, `PERSONAL_OS_*`) |
| Processing | `~/.hermes/skills/daily_stream/processor.py` | `src/personal_os/core/processor.py` (KnowledgeExtractor/Applier) |
| Search | `~/.hermes/skills/search_vault/*` AND `src/personal_os/skills/search.py` (tantivy) | `src/personal_os/core/search.py` (ripgrep + fallback) |
| Note linking | `~/.hermes/skills/link_notes/linker.py` | `src/personal_os/core/links.py` (LinkGraph) |
| Weekly review | `~/.hermes/skills/weekly_review/generator.py` | `src/personal_os/core/weekly_review.py` (WeeklyReviewGenerator) |
| Voice capture | `~/.hermes/skills/voice_capture/*` | `src/personal_os/skills/voice_capture.py` |
| Project creation | `~/.hermes/skills/create_project/*` | `src/personal_os/core/vault.py::create_project` |
| Skill registry | `Scripts/skill_registry.json` | removed (no registry in new arch) |

## CLI command coverage

New `pos` (Typer) commands vs legacy `Scripts/personal-os` (argparse) + Hermes skills:

| Legacy command / skill | New `pos` command | Status |
|------------------------|------------------|--------|
| `capture` (text) | `pos note <text>` | ✅ parity |
| `today` | `pos today` | ✅ parity |
| `projects` | `pos projects` | ✅ parity |
| `tasks` | `pos tasks` | ✅ parity |
| `search` | `pos search` (+ `--project`/`--type`) | ✅ superset (query syntax, ripgrep) |
| `config` | `pos config` | ✅ parity |
| `init` | `pos init` | ✅ parity |
| `review` (weekly) | `pos weekly-review` (+ `pos review` alias) | ✅ superset (structured ReviewData + dashboards) |
| `link` | `pos links` (suggest/related/orphans/hub) | ✅ superset |
| `voice` | `pos voice` | ✅ parity (delegates to `skills/voice_capture.py`) |
| — (n/a) | `pos new-project` / `open-project` | ✅ new (project mgmt) |
| — (n/a) | `pos process <text>` | ✅ new (expose processor) |
| — (n/a) | `pos` (no args) → TUI | ✅ new (Textual TUI) |
| create_project skill | `pos new-project` (vault.create_project) | ✅ parity |

## Reusable services (callable without CLI)

- `core.processor.process_entry(text, source)` — NLP extraction + vault apply.
- `core.search.search(query, limit)` / `SearchEngine` — used by CLI + TUI SearchPanel.
- `core.links.LinkGraph` — `suggest_links`, `find_related`, `find_orphans`, `create_hub`.
- `core.weekly_review.WeeklyReviewGenerator` / `build_review_data` / `generate_review` — structured data + rendering.
- `core.vault.VaultManager` — journal append, project create, progress/task reading.
- `skills.voice_capture` — record/transcribe (whisper.cpp external).

All are driven by the canonical `VaultManager`/`VaultConfig`. No hardcoded vault paths remain in migrated code.

## Remaining functional gaps

1. **Daily Brief / auto-processing trigger.** The legacy `daily_stream` ran on a
   schedule inside Hermes (conversational capture). The new `core.processor` is the
   reusable engine, but there is no standalone `pos daily-brief` command yet. This is
   forward-looking (the ReviewData model was designed to support Daily Briefs /
   Monthly / dashboards / AI summaries) — not a regression, but not yet exposed.

2. **Hub files are indexed by later analyses.** `create_hub` writes a `.md` note under
   `05-Resources/Hubs/`, which subsequent `suggest_links`/`find_related` re-index. This
   is correct (hubs are notes) but means a hub can surface itself as a suggestion.
   Acceptable; noted for awareness.

3. **Voice capture depends on external whisper.cpp** (`/usr/bin/whisper-cli` on NixOS).
   Same constraint as legacy; not a regression. `pos voice` degrades gracefully
   (logs error, returns None) when whisper.cpp or the model is absent.

4. **No surprise git commits.** Legacy skill layer auto-committed the vault
   (`git add -A && commit`). The new package does NOT auto-commit — by design (the
   user flagged surprise commits as a bug). Vault writes are local-file only.

5. **`Templates/daily-template.md` and `Templates/meeting-template.md`** still contain
   Obsidian-Templater syntax and are NOT consumed by any application code (only
   `project-template.md` is canonical). They remain as authoring references and should
   be migrated to `{{var}}` syntax when integrated. Documented as a TODO, not removed.

6. **Legacy `~/.hermes/skills/*` retained.** The daily_stream / update_project /
   create_project / search_vault / voice_capture / link_notes / weekly_review copies
   in the Hermes home profile are DEPRECATED / SUPERSEDED by the package but are left
   untouched (out of repository scope, external runtime). They are now redundant.

## Verification done during migration (ad-hoc, isolated vaults)

- #2 junk removal + alias fix: 10/10 checks pass.
- #3 template unification: canonical `{{project_name}}` template renders; no tokens left; existing notes untouched.
- #4 link_notes: 13/13 checks pass (read-only safe, hub valid, duplicate hub graceful, deterministic).
- #5 weekly_review: 21/21 checks pass (structured data before render, all sections, deterministic overwrite, existing untouched).
- #6 duplicate search: 8/8 checks pass (duplicate removed; `pos search` routed to `core.search`; `--project` works).

## Conclusion

Feature parity is achieved for every user-facing capability that previously lived in
the legacy implementation. The only differences are intentional improvements (single
canonical engine per concern, no surprise commits, structured review data, editor-agnostic
templates). Remaining items (#1 daily-brief exposure, #5 template migration of daily/meeting)
are enhancements, not regressions.
