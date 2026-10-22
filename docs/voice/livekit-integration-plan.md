# LiveKit Voice Integration Plan for Raphael

> **Status:** Investigation complete — prototype runnable locally.  
> **Date:** 2026-10-21  
> **SDK verified:** `livekit-agents==1.8.8` (current stable, 1.x `AgentSession` API)

---

## 1. Architecture and Data Flow

### Current Text Path (verified against repo)

```
Browser (TanStack Start / React 19)
  └─ RaphaelChatPage [ui/src/routes/raphael.tsx]
       └─ RaphaelApiClient.sendMessage() [ui/src/lib/raphael-api.ts]
            └─ POST /api/v1/raphael/conversations/{id}/messages
                 └─ send_message() [api/routes/raphael.py]
                      └─ RaphaelService.send_message() [services/raphael.py]
                           ├─ if assistant_backend == "bedrock":
                           │    └─ BedrockAssistantEngine.run_turn() [services/assistant.py]
                           │         └─ AWS Bedrock (Nova 2 Lite) + PersonalOSToolExecutor
                           └─ else (hermes / mock):
                                └─ LiveHermesClient → hermes CLI subprocess
                           └─ _process_task_actions() → TaskService / ProjectService
                                └─ PostgreSQL (via SQLAlchemy async)
```

**Key observation:** `RaphaelService.send_message()` dispatches to either `BedrockAssistantEngine` (current default via `ASSISTANT_BACKEND=bedrock`) or `LiveHermesClient` (subprocess) depending on `settings.assistant_backend`. Conversation and message rows are always persisted in PostgreSQL regardless of backend. Action parsing (`_process_task_actions`) runs after the assistant responds and mutates tasks/projects via `TaskService`/`ProjectService`.

### Proposed Voice Path

```
Browser (microphone + speaker)
  └─ @livekit/components-react (LiveKit JS SDK)
       └─ WebRTC audio track → LiveKit Room
            └─ LiveKit Server (livekit-server, self-hosted locally / cloud for staging)
                 └─ LiveKit Agents Worker [experiments/livekit-voice/]
                      ├─ VAD (Silero) — silence / speech detection
                      ├─ STT (Deepgram / OpenAI Whisper) — ASR → text transcript
                      ├─ LLM turn (calls Personal OS FastAPI)
                      │    └─ POST /api/v1/raphael/conversations/{id}/messages
                      │         (Bearer token: worker service token, bound to user session)
                      ├─ TTS (Cartesia / OpenAI TTS) — text → audio frames
                      └─ audio track back to browser → speaker
```

### Token / Session Binding

```
User browser
  1. Authenticates with Personal OS API (Cognito JWT / dev token)
  2. Calls POST /api/v1/voice/token  ← NEW endpoint (Stage B)
       • Backend validates Cognito JWT
       • Issues short-lived LiveKit access token (room-scoped, user-scoped)
       • Returns {livekit_url, livekit_token, conversation_id}
  3. Browser connects to LiveKit room using token
  4. Agent worker joins same room, reads metadata (user_id, conversation_id)
  5. Worker calls Personal OS API with its own bearer token (internal service token)
```

### Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Browser                                                                │
│  ┌──────────────────┐    WebRTC audio      ┌───────────────────────┐   │
│  │ Microphone       │ ──────────────────►  │                       │   │
│  │ (getUserMedia)   │                      │   LiveKit Server      │   │
│  │                  │ ◄──────────────────  │  (local / cloud)      │   │
│  │ Speaker          │    WebRTC audio      │                       │   │
│  └──────────────────┘                      └──────────┬────────────┘   │
│                                                        │               │
└────────────────────────────────────────────────────────┼───────────────┘
                                                         │ agent SDK
                                              ┌──────────▼────────────┐
                                              │  Agents Worker        │
                                              │  ┌─────────────────┐  │
                                              │  │ VAD (Silero)    │  │
                                              │  │ STT (Deepgram)  │  │
                                              │  │ TTS (Cartesia)  │  │
                                              │  └────────┬────────┘  │
                                              │           │ text turn  │
                                              └───────────┼────────────┘
                                                          │ HTTP
                                              ┌───────────▼────────────┐
                                              │  Personal OS FastAPI   │
                                              │  /api/v1/raphael/...   │
                                              │  ┌─────────────────┐   │
                                              │  │ RaphaelService  │   │
                                              │  │ + Bedrock/Hermes│   │
                                              │  │ + TaskService   │   │
                                              │  └────────┬────────┘   │
                                              │           │             │
                                              │        PostgreSQL       │
                                              └────────────────────────┘
```

---

## 2. Ownership Boundaries

| Concern | Owner | Notes |
|---------|-------|-------|
| Real-time voice transport, WebRTC, jitter buffer | LiveKit | Never write raw audio to database |
| Voice activity detection | LiveKit Agents (Silero VAD) | Configurable per-session |
| End-of-turn detection | LiveKit Agents (default: EOU model or energy-based) | Tunable silence threshold |
| Barge-in / interruption | LiveKit Agents (`allow_interruptions=True`) | Agent cancels TTS and re-listens |
| ASR transcript → text | STT plugin (Deepgram / Whisper) | Text passes to Raphael; audio never stored |
| Conversation intent, orchestration, context | Raphael / RaphaelService | Unchanged from text path |
| LLM reasoning, tool use | BedrockAssistantEngine or Hermes | Unchanged |
| Task / project mutations | TaskService, ProjectService (PostgreSQL) | Authorization check enforced server-side |
| Conversation persistence (turns, history) | PostgreSQL `conversation_messages` | Voice turns write same rows as text turns |
| Access tokens for LiveKit rooms | Personal OS FastAPI (server-side only) | Never expose LiveKit API secret to browser |
| Microphone permission | Browser (explicit user grant) | Documented in security section |
| Provider API keys | Environment variables on the worker host | Never in browser bundle |

**Invariant:** the browser receives only a short-lived, room-scoped LiveKit access token — never a LiveKit API secret, Bedrock key, Deepgram key, or database credential.

---

## 3. Technology Decisions

### 3.1 LiveKit Agents SDK Version

**Verified installed:** `livekit-agents==1.8.8` (1.x stable, `AgentSession` API).

The 1.x SDK uses a unified `AgentSession` that supports both the classic **cascaded pipeline** (STT → LLM → TTS) and **speech-to-speech** multimodal models (OpenAI Realtime, Gemini Live) under the same interface. This is a strict improvement over the 0.12 `VoicePipelineAgent`.

```python
# 1.x AgentSession pattern (used in prototype)
from livekit.agents import AgentSession, Agent, JobContext
```

### 3.2 Speech-to-Text

| Option | Latency | Free tier | Notes |
|--------|---------|-----------|-------|
| **Deepgram Nova-2** | ~150–300ms | $200 free credit | Recommended for low-latency prod |
| **OpenAI Whisper (plugin)** | ~400–800ms | $0.006/min | Easiest to start; no extra account |
| **Local Whisper (openai-whisper)** | 1–3s on CPU | Free | Already in pyproject `voice` extra |

**Recommendation for prototype:** OpenAI Whisper plugin (single API key already likely available). For Stage B, switch to Deepgram for acceptable turn latency.

### 3.3 Text-to-Speech

| Option | Latency | Cost | Notes |
|--------|---------|------|-------|
| **OpenAI TTS** | ~400ms | $0.015/1k chars | Easiest; single API key |
| **Cartesia Sonic** | ~80–150ms | Free trial | Best latency; recommended for Stage B+ |
| **ElevenLabs** | ~200–400ms | Free tier | Good quality |

**Recommendation for prototype:** OpenAI TTS (single key). Stage B: Cartesia.

### 3.4 Voice Activity Detection

Silero VAD is bundled free with `livekit-agents[silero]`. It is a small (2 MB) ONNX model that runs entirely on CPU with ~5ms latency. It is the correct default — no provider account needed.

### 3.5 Speech-to-Speech (S2S) vs. Cascaded Pipeline

**Cascaded** (STT → Raphael → TTS): text passes through `RaphaelService` unmodified — task actions, conversation persistence, authorization, and tool use all remain in place. This is the correct choice for Stage A and B.

**S2S** (OpenAI Realtime / Gemini Live): the model generates speech directly, bypassing text. This would require bypassing or wrapping `RaphaelService`, risking unauthorized task mutations or lost history. **Do not use S2S until Raphael's action authorization is bridged to work with S2S tool calling.** Document this as an open decision.

### 3.6 Hermes vs. Bedrock for Voice Turns

**Hermes (CLI subprocess):**
- Each turn spawns a new process via `asyncio.create_subprocess_exec`.
- Startup latency: ~1–3s (CLI init, model load).
- Session continuity: maintained via `hermes_session_id` (stored in Conversation row).
- **Verdict:** Acceptable for Stage A prototype where latency is not critical. Unsuitable for production voice at < 2s TTFA (time-to-first-audio) targets.

**Bedrock (`ASSISTANT_BACKEND=bedrock`, default):**
- HTTP call to AWS Bedrock; latency ~300–800ms per turn.
- Context: full conversation history fetched from DB and passed on every turn.
- Tool calling loop bounded by `max_tool_turns=5`.
- **Verdict:** Preferred for voice. Worker loads conversation history from DB, calls `POST /api/v1/raphael/conversations/{id}/messages`, returns text to TTS.

**Recommendation:** Use `ASSISTANT_BACKEND=bedrock` for voice prototype. Hermes can be tested separately. The worker calls Raphael's HTTP endpoint rather than importing Python modules — this is a clean seam.

### 3.7 Infrastructure Required (Local)

| Component | Local development | Notes |
|-----------|-------------------|-------|
| LiveKit Server | `livekit-server --dev` | Free binary, no account needed |
| Personal OS FastAPI | Existing `uvicorn` dev server | No changes |
| Agent Worker | `python agent.py dev` | New file in experiments/ |
| PostgreSQL | Existing docker-compose | No changes |

**No paid services required for Stage A prototype.** Provider API keys are optional; the prototype falls back gracefully with printed errors.

---

## 4. Conversation and Turn-Taking

### 4.1 Voice Activity Detection

Silero VAD monitors the microphone audio stream. When RMS energy exceeds the threshold, a `VAD_START` event fires and the agent begins collecting audio. When energy drops below threshold for `min_silence_duration` (default 0.6s), `VAD_END` fires and the collected audio is sent to STT.

### 4.2 End-of-Turn Detection

LiveKit Agents 1.x uses an **End-of-Utterance (EOU)** model by default when available (plugin: `livekit-plugins-turn-detector`). This uses a small language model to predict "is the user done speaking?" instead of relying on pure silence. Without the EOU plugin, pure energy-based VAD is used.

**Recommendation:** Start with pure VAD (no extra dependency). Add EOU model in Stage B if false turn detections are frequent.

### 4.3 Barge-In (User Interrupts Raphael)

When `allow_interruptions=True` (the default in `AgentSession`):
1. VAD detects user speech while TTS audio is playing.
2. LiveKit Agents cancels the current TTS playback immediately.
3. The agent transitions to `listening` state.
4. The partial LLM response is discarded.
5. No assistant message is written to the database for the cancelled turn.

**Implementation note:** The worker must check whether an assistant message has already been persisted before allowing a retry that re-calls `RaphaelService.send_message()`. The existing `client_message_id` idempotency mechanism handles this correctly — if the user message was already committed, the agent should not send the same `client_message_id` again.

### 4.4 Natural Pause Handling

A 250ms natural pause inside a sentence should **not** trigger end-of-turn. Configure:
```python
silero.VAD.load(min_silence_duration=0.8)  # 800ms silence before end-of-turn
```

This is longer than a breathing pause but shorter than a true turn end. Adjust based on testing.

### 4.5 Cancellation of Active Response Generation

When barge-in occurs:
1. TTS audio output is cancelled by the Agents framework.
2. If an in-flight HTTP call to `POST .../messages` is still running, the worker should cancel it (using `asyncio.Task.cancel()`).
3. The user message may or may not have been written to the DB at this point. If it was written and the assistant message was not, the next turn will find the orphaned user message — this is acceptable; the user can simply speak again.

### 4.6 Silence, Background Noise, Echo

- Use `min_silence_duration` tuning to prevent background noise from triggering false turns.
- Enable browser-side **acoustic echo cancellation** (AEC) via `getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })` in the frontend.
- LiveKit's WebRTC stack uses AEC by default at the signaling level.
- The Silero VAD model has built-in noise robustness.

### 4.7 Recovery from Disconnects

- If the WebRTC connection drops, the browser should attempt reconnection using the LiveKit JS SDK's built-in reconnect logic.
- The LiveKit access token is short-lived (15 minutes recommended). Issue a new token when reconnecting.
- Conversation history is always in PostgreSQL — reconnecting after a disconnect picks up the same `conversation_id` without data loss.

### 4.8 Preventing Duplicate Task Actions on Retry

Task creation idempotency is enforced both in application logic and at the database level:
1. **Deterministic Turn ID:** The voice worker generates a deterministic, room-scoped `client_message_id` per utterance turn: `voice-{room_name}-{turn_counter}`.
2. **Conversation Cache:** In `RaphaelService.send_message()`, if both user message and assistant reply exist for that `client_message_id`, the response is immediately returned from the message cache (**~7ms**) without invoking the LLM or task executor.
3. **Targeted DB Query (No In-Memory Scan):** `_find_existing_task()` queries PostgreSQL directly against `Task.extraction_meta['client_message_id'].as_string() == client_message_id`, eliminating the legacy `limit(25)` scan and remaining reliable even across thousands of intervening tasks.
4. **Database-Level Partial Unique Index:** Migration `0010_task_client_id_unique.py` enforces a partial unique constraint on active tasks:
   ```sql
   CREATE UNIQUE INDEX uq_tasks_owner_client_message_id
   ON tasks (owner_id, (extraction_meta->>'client_message_id'))
   WHERE extraction_meta IS NOT NULL 
     AND extraction_meta->>'client_message_id' IS NOT NULL;
   ```
5. **Concurrent Collision Handling:** In `_execute_create_task()`, task creation is enclosed in a database savepoint (`begin_nested()`). If a concurrent request commits first, the resulting `IntegrityError` triggers a savepoint rollback and immediately resolves to the existing task record, avoiding unhandled exceptions or duplicate records. Null `client_message_id` values remain unrestricted.

---

## 5. Security & Voice Authorization Design

### 5.1 Architecture & Token Lifecycle

```
[Authenticated Browser]
       │
       │ 1. POST /api/v1/voice/token { conversation_id } (with User Cognito JWT)
       ▼
[Personal OS API Control Plane]
       │ Validates user ownership of conversation_id (404 if unauthorized)
       │ Generates 15-minute LiveKit room access token with claims:
       │    identity: user_id
       │    room: pos-voice-{conversation_id}
       │    metadata: { user_id, conversation_id }
       │ Generates short-lived session delegation token for worker
       │
       ├─────────────────────────────────────────┐
       ▼ (livekit_url, livekit_token)             ▼ (dispatches room job)
[Browser WebRTC Client]                  [LiveKit Agents Worker]
       │                                         │
       │ Connects to room                        │ Joins room, reads metadata
       └──────────────► [LiveKit Server] ◄───────┘
                               │
                               │ Audio Track (WebRTC)
                               ▼
                    [Agent Transcribes Audio]
                               │
                               │ 2. POST /api/v1/raphael/conversations/{id}/messages
                               │    (Presents short-lived delegated session token)
                               ▼
                 [Personal OS Raphael API]
                 (Enforces user_id == owner_id strictly)
```

### 5.2 Key Authorization Invariants

1. **Zero Credential Exposure to Browser:**
   * The browser **never** receives LiveKit API secrets, backend master bearer tokens, provider API keys (OpenAI / Deepgram / Cartesia / Bedrock), or database credentials.
   * The browser receives only a short-lived (15-minute) signed LiveKit access token with join permissions scoped to its specific room.
2. **No Shared Production Worker Token:**
   * The worker does not use a single shared superuser token in production.
   * Instead, the worker receives a session delegation token generated specifically for that room assignment, strictly binding its actions to the authenticated user's ULID.
3. **Strict Scoping & Tenant Isolation:**
   * `owner_id` is derived exclusively from the authenticated session context via `get_current_user()` and cannot be passed or overridden in request payloads.
   * Cross-tenant access is rejected with `404 Not Found` (verified by automated tests).
4. **Token Generation Endpoint Pre-Requisite:**
   * The proposed `POST /api/v1/voice/token` endpoint will not be deployed to production until its authentication contract is reviewed and verified with automated integration tests.

### 5.4 Microphone and Audio Retention

- Microphone audio is sent over WebRTC to LiveKit Server and then to the worker over the agent channel.
- Audio is **not** stored anywhere by default. Only the STT transcript (text) is stored in PostgreSQL.
- Inform users in the UI that microphone access is required and that audio is processed by the selected STT provider (Deepgram or OpenAI).
- LiveKit's SFU does not log media by default. Do not enable recording without explicit user consent.

### 5.5 HTTPS / WSS Requirement

The browser only grants `getUserMedia` in secure contexts (HTTPS or localhost). Local dev on `http://localhost` is fine. Any deployment must use HTTPS.

---

## 6. Implementation Stages

### Stage A: Isolated Local Voice Prototype

**Goal:** Prove that audio can flow browser → LiveKit → worker → LLM → TTS → browser with interruptions working.

**Files to create:**
- `experiments/livekit-voice/agent.py` — LiveKit Agents worker
- `experiments/livekit-voice/requirements.txt` — isolated dependencies
- `experiments/livekit-voice/.env.example` — credential template
- `experiments/livekit-voice/README.md` — setup instructions
- `experiments/livekit-voice/test_report.md` — test results

**Dependencies introduced:** `livekit-agents[openai,silero]` (isolated, not added to main pyproject.toml yet)

**Acceptance criteria:**
- Worker connects to local LiveKit server.
- Browser microphone audio is captured and sent to worker.
- Worker responds with TTS audio played in the browser.
- User can interrupt the agent while it is speaking (barge-in).
- VAD correctly ignores 300ms mid-sentence pauses.
- Missing credentials produce actionable error messages.

**Risks:**
- Silero VAD downloads a model file (~2 MB) on first run. Requires internet.
- LiveKit Server binary must be installed locally.

**Rollback:** delete `experiments/livekit-voice/`. No changes to existing code.

---

### Stage B: Connect Prototype to Raphael

**Goal:** Replace placeholder agent with full Raphael orchestration. Voice turns write to the same conversation as text turns.

**New files:**
- `src/personal_os/api/routes/voice.py` — LiveKit token endpoint (`POST /api/v1/voice/token`)
- `experiments/livekit-voice/raphael_agent.py` — Raphael-connected worker

**Modified files:**
- `src/personal_os/api/main.py` — register `voice_router`
- `src/personal_os/api/routes/__init__.py` — export `voice_router`
- `src/personal_os/config.py` — add `livekit_api_key`, `livekit_api_secret`, `livekit_url`, `worker_api_token`

**Acceptance criteria:**
- A spoken "create a task to write tests" results in a task row in PostgreSQL.
- Two consecutive voice turns share the same conversation and context.
- The same `client_message_id` mechanism prevents duplicate tasks on retry.
- Text chat still works after these changes.

**Risks:**
- Hermes latency (1–3s process startup) may make voice feel sluggish. Document and use Bedrock backend.
- Worker credentials must not be committed.

**Rollback:** remove `voice.py`, revert `main.py` and `config.py`.

---

### Stage C: Minimal Voice UI

**Goal:** Add a small voice toggle button to the existing Raphael chat page.

**New files:**
- `ui/src/components/voice-session.tsx` — voice mode toggle with microphone indicator
- `ui/src/lib/voice-api.ts` — fetch LiveKit token from `/api/v1/voice/token`

**Modified files:**
- `ui/src/routes/raphael.tsx` — add voice toggle button; render `VoiceSession` when active
- `ui/package.json` — add `@livekit/components-react`, `livekit-client`

**Acceptance criteria:**
- Clicking "Voice" requests microphone permission.
- User speaks; response plays as audio.
- Barge-in stops playback within 300ms of speech start.
- Existing text chat is unaffected (voice mode is opt-in).

**Rollback:** revert `raphael.tsx`, remove new files, remove npm packages.

---

### Stage D: Persistence, Security, Reliability

**Goal:** Harden the integration for real use.

**Changes:**
- Add integration tests for the voice token endpoint (`tests/test_voice_token.py`).
- Add token expiry validation and refresh in the frontend.
- Add a test verifying a voice action cannot bypass `owner_id` scoping.
- Document audio retention policy in the UI (`docs/voice/privacy.md`).
- Tune VAD silence threshold based on Stage A/B test recordings.

**Acceptance criteria:** All 7 verification tests pass or are marked explicitly as requiring manual testing with documented reasons.

---

### Stage E: Staging Deployment (Requires Approval)

> **This stage requires explicit written approval before any work begins.**

**Decision required:**
- Where to host LiveKit Server: LiveKit Cloud (free tier for dev, paid for prod) or self-hosted on existing EC2.
- Which STT/TTS providers to use in production and the cost model.
- Whether to add a new ECS task for the agent worker or run it on the existing EC2 instance.

**Estimated monthly cost at low volume:**
- LiveKit Cloud: $0 free tier (10k participant-minutes/month), then ~$0.002/min.
- Deepgram STT: $0.0059/min (~$3 for 500 min/month).
- Cartesia TTS: $0.01/1k chars (~$5 for 500k chars/month).
- OpenAI TTS alternative: $0.015/1k chars.

No AWS resources should be modified until Stage E is explicitly approved.

---

## 7. Open Decisions

| Decision | Options | Recommendation |
|----------|---------|----------------|
| STT provider | Deepgram / OpenAI Whisper / local | OpenAI Whisper for prototype, Deepgram for Stage B+ |
| TTS provider | OpenAI TTS / Cartesia / ElevenLabs | OpenAI TTS for prototype, Cartesia for Stage B+ |
| S2S model | OpenAI Realtime / Gemini Live | Do not use until action authorization is bridged |
| EOU model | Silero VAD only / turn-detector plugin | VAD only for prototype; evaluate EOU in Stage B |
| Worker hosting | EC2 / ECS task / local | Local only until Stage E approval |
| LiveKit hosting | LiveKit Cloud / self-hosted | Local (`livekit-server --dev`) until Stage E |

---

## 8. Files Changed Summary

| File | Stage | Change type |
|------|-------|-------------|
| `experiments/livekit-voice/agent.py` | A | New |
| `experiments/livekit-voice/requirements.txt` | A | New |
| `experiments/livekit-voice/.env.example` | A | New |
| `experiments/livekit-voice/README.md` | A | New |
| `experiments/livekit-voice/test_report.md` | A | New |
| `experiments/livekit-voice/raphael_agent.py` | B | New |
| `src/personal_os/api/routes/voice.py` | B | New |
| `src/personal_os/api/main.py` | B | Add router |
| `src/personal_os/api/routes/__init__.py` | B | Export |
| `src/personal_os/config.py` | B | Add 4 settings |
| `ui/src/components/voice-session.tsx` | C | New |
| `ui/src/lib/voice-api.ts` | C | New |
| `ui/src/routes/raphael.tsx` | C | Add toggle |
| `ui/package.json` | C | Add 2 deps |
| `tests/test_voice_token.py` | D | New |
| `docs/voice/privacy.md` | D | New |

---

## Appendix: Hermes Suitability Assessment

**Hermes is unsuitable for real-time voice in its current form** because:
1. Each invocation starts a new OS process, adding 1–3s of latency before any model inference.
2. There is no streaming response — the full reply is buffered before returning.
3. Session continuity depends on `hermes_session_id` which requires a DB lookup on every turn.

**Alternatives if Hermes must be used:**
- Pre-warm a persistent Hermes process and communicate over stdin/stdout (significant complexity).
- Use Hermes only for intent classification while using a lighter model for voice response generation.
- Accept the latency penalty for an MVP demo where low-latency is not required.

**Recommended path:** Use `ASSISTANT_BACKEND=bedrock` for voice. Hermes remains unchanged for text chat.
