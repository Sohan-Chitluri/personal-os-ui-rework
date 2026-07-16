# Active Tasks

## Today's Focus (MITs - Most Important Tasks)
- [x] Complete Phase 1: Foundation - Terminal Widget MVP
- [x] Build daily_stream skill processing logic (terminal-based)
- [x] Create terminal UI widget for daily capture
- [x] Implement core skills: update, search, create, capture
- [x] Test terminal widget end-to-end (capture, today, skills, process)
- [x] Install whisper (openai-whisper) for voice integration
- [x] **Refine automatic processing pipeline** - auto-process after every capture
- [x] **Fix processor.py** - all f-string issues resolved

## Phase 1: Core Personal OS ✅ COMPLETE
### Core Infrastructure
- [x] Terminal-based daily stream widget (POSIX shell / Python)
- [x] Raw entry storage to Journal/YYYY-MM-DD.md
- [x] Core capabilities implemented:
  - [x] **capture** — Natural language intake, raw chronological stream
  - [x] **update** — NLP extraction + routing to projects, tasks, decisions
  - [x] **search** — Global markdown search with filters (project:, type:, tag:, date:)
  - [x] **create** — Project scaffolding with standardized templates

### Skills Implemented (7/11)
- [x] **update** — Project file updates with thinking workflow (SEARCH→READ→THINK→EDIT→SUMMARIZE)
- [x] **capture** — Daily stream capture (voice-ready, text fallback)
- [x] **create** — Project scaffolding with 8 standard files
- [x] **search** — Global markdown search with filters (project:, type:, tag:, date:)
- [x] **review** — Evening reflection + next-day planning
- [x] **link** — Knowledge graph building and maintenance
- [x] **voice** — Voice capture placeholder (whisper.cpp ready)

## Phase 2: Voice & Natural Interaction (Next Priority)
### Voice Integration
- [ ] Install audio stack (arecord, ffmpeg, pulseaudio)
- [ ] Create push-to-talk capture script
- [ ] Integrate whisper with daily_stream for continuous capture
- [ ] Voice summaries (daily/weekly)
- [ ] Hands-free interaction mode

### Natural Interaction
- [ ] Single "talk to Hermes" interface (no commands)
- [ ] Intent recognition from natural language
- [ ] Automatic skill selection (capture vs update vs search vs create)
- [ ] Context-aware responses

## Phase 3: Intelligence
### Reviews & Insights
- [ ] Weekly review (automated metrics, insights, planning)
- [ ] Daily review (evening reflection, next-day MITs)
- [ ] Knowledge consolidation (continuous)
- [ ] Suggestions (proactive based on patterns)

### Pattern Recognition
- [ ] Smart prioritization (deadlines, energy, context)
- [ ] Blocker detection and alerts
- [ ] Recurring work identification
- [ ] Energy/mood correlation with productivity

## Phase 4: Automation & Integrations (Ongoing)
- [ ] Calendar integration (read-only)
- [ ] GitHub integration (issues, PRs)
- [ ] External tools (Vivado, Git, academic portals)
- [ ] Optional automation rules
- [ ] Multi-device improvements

## Inbox (to be processed)
- [ ] Review Personal OS constitution for completeness
- [ ] Define standard project file structure
- [ ] Design skill approval/preview system for risky operations
- [ ] Document skill development guidelines

## Waiting For
- [ ] Feedback on skill-based approach from future-self
- [ ] Clarification on capability granularity
- [ ] Decision on voice integration approach (whisper.cpp vs API)
- [ ] Audio stack installation and testing

## Capabilities Development Progress
- [x] **capture** — Daily stream capture (voice-ready, text fallback)
- [x] **update** — Project file updates with thinking workflow
- [x] **search** — Global search with filters and ranking
- [x] **create** — Project scaffolding from templates
- [x] **review** — Evening reflection and next-day planning
- [x] **link** — Knowledge graph building
- [x] **voice** — Voice capture placeholder (whisper.cpp ready)
- [ ] **weekly_review** — Automated metrics and insights
- [ ] **task_management** — Cross-project task operations
- [ ] **meeting** — Structured meeting notes and follow-ups
- [ ] **archive** — Completed items preservation

## Projects Overview
- [[02-Projects/personal-os]] - Personal Operating System (this project)
- [[01-Academic/Current-Semester]] - Academic coursework
- [[02-Projects/Team-NAMO]] - Team NAMO projects and commitments

---
*View filters:*
- `tag:#personal-os` | `tag:#academic` | `tag:#namo` 
- `due:today` | `due:this week` | `overdue`
- `status:active` | `status:planning` | `status:blocked`
- `capability:#capture` | `capability:#update` | `capability:#search` | `capability:#create`
- `phase:#1` | `phase:#2` | `phase:#3`