# Session Knowledge Base - Personal OS Project

> **Purpose**: Capture all knowledge gained during this chat session for future reference across sessions.

---

## Project Overview

**Personal Operating System (Personal OS)** — A terminal-first, markdown-vault-based personal operating system managed by Hermes (as Morales) through natural conversation.

**Architecture**: Hermes-First, Voice-First, Local-First, Markdown-Vault-as-Source-of-Truth

**Core Capabilities** (4 Core Capabilities):
1. **capture** — Natural language intake, raw chronological stream (Daily Stream)
2. **update** — NLP extraction + routing to projects, tasks, decisions
3. **search** — Global markdown search with filters (project:, type:, tag:, date:)
4. **create** — Project scaffolding with standardized templates

---

## Project Structure

```
/home/peskybird/projects/personal-os/
├── CONSTITUTION.md          # Core principles, architecture, workflows
├── ROADMAP.md               # Phase 1-4 roadmap with checkboxes
├── TASKS.md                 # Active tasks, MITs, project overview
├── SESSION-KNOWLEDGE.md     # This file - session knowledge capture
├── Scripts/
│   ├── personal-os          # Main CLI entry point (pos command)
│   └── skill_registry.json  # Skill registry with triggers & priorities
├── 00-Inbox/                # Inbox for unprocessed items
├── 01-Academic/             # Academic work (VIT Chennai, 2024-2028)
├── 02-Projects/             # Active projects
│   ├── personal-os/         # This project
│   └── test-project/        # Example project with 8-file structure
├── 03-Skills/               # Skill tracking (VLSI, etc.)
├── 04-Personal/
│   └── Journal/             # Daily journals: YYYY-MM-DD.md
├── 05-Resources/            # Reference materials
├── 06-Archive/              # Archived/completed work
└── Templates/               # Project templates
```

---

## Skills Implemented (Hermes Skills at ~/.hermes/skills/)

| Skill | Status | Purpose | Triggers |
|-------|--------|---------|----------|
| `daily_stream` | ✅ Done | Primary capture → extraction → routing | capture, log, note, remember, i spent, i worked, i think, i wonder, we decided, i finished, i completed |
| `update_project` | ✅ Done | Safe project file updates with thinking workflow | update project, change project, project update, remember that, we decided, switched to |
| `create_project` | ✅ Done | Project scaffolding with 8-file structure | create project, new project, start project |
| `search_vault` | ✅ Done | Global markdown search with filters | search, find, find project, look for |
| `review` | ✅ Done | Evening reflection + next-day MITs | review, daily review, evening review, plan tomorrow |
| `link_notes` | ✅ Done | Knowledge graph building & maintenance | link, connect, relate, associate |
| `voice_capture` | ✅ Done | Voice capture placeholder (whisper.cpp ready) | voice, speak, record, whisper |

**Skill Registry**: `/home/peskybird/projects/personal-os/Scripts/skill_registry.json`
- Processing order: `daily_stream` → `update_project`
- Fallback: `daily_stream`

---

## CLI Tool: `pos` (Personal OS)

**Location**: `/home/peskybird/projects/personal-os/Scripts/personal-os`

**Commands**:
```bash
pos capture "entry text"       # Capture entry + auto-process
pos capture                    # Interactive capture (Ctrl+D to finish)
pos capture --voice            # Voice capture (whisper.cpp)
pos today                      # Show today's journal
pos process                    # Process today's entries
pos process --batch            # Batch process all today
pos process --dry-run          # Show what would be extracted
pos search "query"             # Search vault
pos search "query" --project maestro  # Filter by project
```

**Key Feature**: `pos capture` automatically processes entry through daily_stream processor after saving.

---

## Automatic Processing Pipeline

### Flow
```
User → pos capture "text" 
    → Append to 04-Personal/Journal/YYYY-MM-DD.md
    → DailyStreamProcessor.extract_knowledge(text)
    → Extract: progress, decisions, tasks, observations, learning, deadlines
    → Route to: Progress.md, Architecture.md, Tasks.md, Notes.md, skill Progress.md
    → Return summary of changes
```

### Processor Script
**Location**: `~/.hermes/skills/daily_stream/scripts/processor.py`

**Class**: `DailyStreamProcessor(VAULT_ROOT)`

**Methods**:
- `extract_knowledge(text)` → List[dict] of extractions
- `apply_extraction(extraction)` → List[str] of changes made
- `_identify_project(text)` → Project name via alias mapping
- `_apply_progress()`, `_apply_decision()`, `_apply_task()`, `_apply_observation()`, `_apply_learning()`, `_apply_deadline()`

**Project Alias Mapping** (in processor):
```python
"maestro": "Maestro", "maestro streaming": "Maestro",
"drone": "Drone", "vlsi": "VLSI", "vlsi design": "VLSI",
"namo": "Team NAMO", "team namo": "Team NAMO",
"personal os": "Personal OS", "personal-os": "Personal OS"
```

---

## Critical Fix: Processor F-String Syntax Errors

**Problem**: Multiple f-string syntax errors in `processor.py` where f-strings were incorrectly split across lines.

**Broken Pattern**:
```python
entry = f"- **{timestamp}**: Studied {topic}'
"
content += f"\n## Learning Log\n{entry}"
progress_file.write_text(f"# {skill_area} Learning Progress...\n" + entry)
```

**Fixed Pattern** (string concatenation):
```python
entry = "- **" + timestamp + "**: Studied " + topic + "\n"
content += "\n## Learning Log\n" + entry
progress_file.write_text("# " + skill_area + " Learning Progress...\n" + entry)
```

**Fixed Locations** (9 total):
1. `_apply_learning` - entry variable (lines ~307-308)
2. `_apply_learning` - content append (lines ~315-317)
3. `_apply_learning` - file write (line ~320)
4. `_apply_progress` - entry variable (lines ~206-207)
5. `_apply_progress` - file write (line ~225)
6. `_apply_decision` - decision_entry (lines ~250-255)
7. `_apply_decision` - file write (lines ~265-266)
8. `_apply_observation` - entry variable (line ~370)
9. `_apply_observation` - file write (line ~378)

**Verification**: `pos capture "test entry"` now processes successfully.

---

## Voice Integration (Phase 2 - Next Priority)

**Status**: whisper.cpp (openai-whisper) installed via pip, audio stack pending.

**Audio Stack Needed** (NixOS):
```bash
nix-env -iA nixpkgs.alsa-utils nixpkgs.sox nixpkgs.pulseaudio
# whisper-cpp via nix: nix-env -iA nixpkgs.whisper-cpp
```

**Voice Capture Workflow**:
1. `pos capture --voice` or wake phrase
2. voice_capture skill records audio (arecord/parec/sox)
3. whisper.cpp transcribes to text
4. Text flows into daily_stream as normal capture
5. daily_stream processes with standard extraction

**Config** (in daily_stream skill):
```json
{
  "sample_rate": 16000,
  "channels": 1,
  "format": "s16le",
  "model": "base",
  "language": "en",
  "silence_threshold": 0.01,
  "silence_duration": 2.0,
  "max_duration": 60
}
```

---

## Project Templates (8-File Standard)

Every project gets:
```
<project>/
├── README.md        # Overview, purpose, status
├── Architecture.md  # Technical design, components, decisions
├── Notes.md         # Meeting notes, ideas, open questions
├── Tasks.md         # Action items and todos
├── Progress.md      # Progress logs and metrics
├── Timeline.md      # Chronological history
├── Decisions.md     # Decision log with rationale
└── Research.md      # References, links, study materials
```

**Templates Location**: `/home/peskybird/projects/personal-os/Templates/`
- `project-template.md`
- `meeting-template.md`
- `daily-template.md`

---

## Current Phase Status

### Phase 1: Core Personal OS ✅ COMPLETE
- ✅ Daily stream capture (terminal + auto-process)
- ✅ Project scaffolding (8-file structure)
- ✅ Core skills: capture, update, search, create, review, link, voice
- ✅ Processor f-string fixes
- ✅ Auto-process after every capture

### Phase 2: Voice & Natural Interaction 🎯 NEXT
- [ ] Audio stack (arecord, ffmpeg, pulseaudio)
- [ ] Push-to-talk capture script
- [ ] Whisper integration with daily_stream
- [ ] Voice summaries (daily/weekly)
- [ ] Hands-free interaction mode
- [ ] Natural intent recognition
- [ ] Automatic skill selection

### Phase 3: Intelligence
- [ ] Automated daily/weekly/monthly reviews
- [ ] Knowledge consolidation (stream → projects)
- [ ] Pattern recognition & suggestions
- [ ] Smart prioritization

### Phase 4: Automation & Integrations (Ongoing)
- [ ] Calendar integration (read-only)
- [ ] GitHub integration
- [ ] External tools (Vivado, PCB fab, academic portals)
- [ ] Multi-device sync

---

## User Identity & Context

**User**: Sohan (Electronics Engineering Student, VIT Chennai, 2024-2028)
**Roles**: 
- Electronics Lead, Team NAMO (Namo Nirvana)
- Technical Focus: VLSI, Embedded Systems, Robotics, Linux, FPGA
- Personal Values: Open source, reproducible workflows, minimalism, continuous learning

**AI Identity**: Morales — AI agent, long-term tech partner/chief of staff. Spider-Man legacy: responsible heroism via technical excellence/helpful partnership.

---

## Key Commands Reference

```bash
# Navigate to project
cd /home/peskybird/projects/personal-os

# Run CLI
./Scripts/personal-os capture "text"
./Scripts/personal-os today
./Scripts/personal-os search "query"

# Test processor directly
cd ~/.hermes/skills/daily_stream
python3 scripts/processor.py

# View skills
ls ~/.hermes/skills/
cat ~/.hermes/skills/daily_stream/SKILL.md
cat ~/.hermes/skills/update_project/SKILL.md

# Edit project files
ls 02-Projects/personal-os/
cat 02-Projects/personal-os/Tasks.md
```

---

## Session Accomplishments (This Chat)

1. ✅ Discovered and mapped the complete Personal OS project structure
2. ✅ Read all core documentation (CONSTITUTION.md, ROADMAP.md, TASKS.md)
3. ✅ Examined CLI tool (`pos`) and automatic processing pipeline
4. ✅ Reviewed all 7 implemented Hermes skills
5. ✅ Identified and documented the processor.py f-string syntax bug fix
6. ✅ Documented voice integration architecture and next steps
7. ✅ Captured user identity, roles, and AI partnership model
8. ✅ Created this knowledge file for cross-session continuity

---

## Next Actions for Future Sessions

1. **Install audio stack** for voice capture (NixOS packages)
2. **Create push-to-talk script** integrating arecord + whisper
3. **Integrate voice_capture skill** with daily_stream for continuous capture
4. **Implement natural intent recognition** (single "talk to Hermes" interface)
5. **Build weekly_review skill** (Phase 3)
6. **Add semantic search** with embeddings (Phase 3)

---

## Related Files to Read Next Session

- `CONSTITUTION.md` — Core principles & architecture
- `ROADMAP.md` — Phase tracking with checkboxes
- `TASKS.md` — Active tasks & MITs
- `~/.hermes/skills/daily_stream/SKILL.md` — Full daily_stream spec
- `~/.hermes/skills/update_project/SKILL.md` — Update workflow
- `Scripts/skill_registry.json` — Skill triggers & processing order
- `~/.hermes/skills/daily_stream/scripts/processor.py` — Extraction logic

---

*Created: 2026-07-14 | Session: Personal OS knowledge capture | Agent: Hermes (Morales)*