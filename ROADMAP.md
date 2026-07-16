# Personal OS Roadmap

## Vision
A conversational-first personal operating system where Hermes (as Morales) manages your markdown vault through natural conversation, reducing cognitive friction and increasing execution focus.

---

## Phase 1 — Core Personal OS
**Target**: 2-3 weeks

### Daily Stream (Capture)
- [x] Raw entry storage to Journal/YYYY-MM-DD.md
- [x] Terminal capture command (`pos capture "..."`)
- [x] Today view (`pos today`)
- [ ] Voice capture with whisper.cpp
- [ ] Push-to-talk hotkey
- [ ] Continuous background capture
- [ ] Voice summaries of recent entries

### Project Management
- [x] Project scaffolding (`create_project` skill)
- [x] Standard 8-file structure (README, Architecture, Notes, Tasks, Progress, Timeline, Decisions, Research)
- [x] Project progress logging
- [x] Task management (create/update/complete)
- [ ] Smart task prioritization
- [ ] Cross-project task views

### Knowledge Updates (Update)
- [x] Project progress extraction from stream
- [x] Decision logging with rationale
- [x] Task completion detection
- [x] Learning progress tracking
- [ ] Fact vs observation distinction
- [ ] Automatic deadline extraction
- [ ] Meeting outcome capture

### Search & Recall
- [x] Global markdown search (`search_vault`)
- [x] Filters: project:, type:, tag:, date:
- [x] Context snippets with line numbers
- [ ] Semantic search (embeddings)
- [ ] "Show me everything about X" natural queries

### Project Creation
- [x] Standard 8-file scaffolding
- [x] Templates with conventions
- [x] Git initialization
- [ ] Template variants (academic, hardware, software, research)

---

## Phase 2 — Voice & Natural Interaction
**Target**: 2-3 weeks

### Voice Integration (Primary Input)
- [ ] Whisper.cpp integration (local, fast)
- [ ] Push-to-talk hotkey (global)
- [ ] Continuous listening mode
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
- [ ] "Hey Hermes, capture..." hotword
- [ ] "Hey Hermes, what's my next task?"
- [ ] "Hey Hermes, remind me to..." → creates task
- [ ] "Hey Hermes, search for..." → voice results

---

## Phase 3 — Intelligence
**Target**: 3-4 weeks

### Reviews
- [ ] Daily review (automated evening summary + tomorrow's MITs)
- [ ] Weekly review (automated metrics, insights, next week planning)
- [ ] Monthly retrospective
- [ ] Quarterly objective setting

### Knowledge Consolidation
- [ ] Daily stream → project updates (automated)
- [ ] Observation → decision pipeline
- [ ] Duplicate detection and merging
- [ ] Orphan note detection
- [ ] Cross-reference suggestions

### Suggestions & Pattern Recognition
- [ ] "You've mentioned STM32 5 times this week"
- [ ] "Maestro streaming bug appears in 3 projects"
- [ ] "You're most productive 9-11am"
- [ ] Context-aware task suggestions

### Smart Prioritization
- [ ] Deadline-aware task ordering
- [ ] Energy-aware scheduling
- [ ] Blocker detection and escalation
- [ ] "Focus on this today" recommendations

---

## Phase 4 — Automation & Integrations
**Target**: Ongoing

### Calendar Integration
- [ ] Read-only Google/CalDav sync
- [ ] Meeting detection from calendar
- [ ] Prep reminders
- [ ] Time blocking suggestions

### GitHub Integration
- [ ] Issue/PR creation from tasks
- [ ] Commit log → progress updates
- [ ] Review requests → task creation
- [ ] Release notes from progress logs

### External Tools
- [ ] Vivado/Vitis log parsing
- [ ] PCB fab order tracking
- [ ] Component inventory sync
- [ ] Academic deadline sync (Canvas/LMS)

### Optional Automation
- [ ] "When X happens, do Y" rules
- [ ] Scheduled report generation
- [ ] Backup verification
- [ ] Sync conflict resolution

### Multi-Device
- [ ] Syncthing/Nextcloud sync
- [ ] Mobile read access
- [ ] Conflict-free merging
- [ ] Offline-first design

---

## Success Metrics by Phase

### Phase 1
- [ ] Daily capture feels instant (<2 seconds)
- [ ] Search finds anything in <1 second
- [ ] Projects stay organized without manual filing
- [ ] User never thinks "where does this go?"

### Phase 2
- [ ] Voice capture is primary input method
- [ ] Hands-free operation for 80% of captures
- [ ] Transcription accuracy >95%
- [ ] Latency <2 seconds from speech to stored entry

### Phase 3
- [ ] Weekly review generates itself
- [ ] Patterns surface without asking
- [ ] Suggestions feel relevant >80% of time
- [ ] Mental overhead measurably reduced

### Phase 4
- [ ] Zero manual sync between tools
- [ ] External events create tasks automatically
- [ ] System feels like a true extension of cognition

---

*Last updated: $(date +%Y-%m-%d)*
*Architecture: Hermes-First, Voice-First, Local-First*