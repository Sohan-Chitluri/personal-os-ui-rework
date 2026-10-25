"""Production LiveKit Voice Agent for Raphael.

Connects real-time WebRTC audio streams to Personal OS Raphael HTTP endpoints.
Delegates reasoning, task/project actions, and conversation persistence to RaphaelService.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from livekit.agents import (
    Agent,
    AgentSession,
    APIConnectOptions,
    JobContext,
    WorkerOptions,
    cli,
)
from livekit.agents import (
    llm as lk_llm,
)

from personal_os.config import Settings, get_settings

log = logging.getLogger("personal_os.voice_agent")


def clean_spoken_text(text: str) -> str:
    """Prepare text for TTS by removing code fences, raw action tags, and markdown markup."""
    cleaned = re.sub(r"```[\s\S]*?```", "", text)
    cleaned = re.sub(r"<action>[\s\S]*?</action>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"[*_~`#]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


class PersonalOSVoiceClient:
    """HTTP client for calling Raphael's conversational endpoint from the voice worker."""

    def __init__(self, base_url: str, api_token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=httpx.Timeout(90.0, connect=5.0),
        )

    async def send_message(
        self,
        conversation_id: str,
        content: str,
        client_message_id: str | None = None,
    ) -> str:
        """Send a user message to Raphael and return the assistant reply."""
        payload: dict[str, Any] = {"content": content}
        if client_message_id:
            payload["client_message_id"] = client_message_id

        url = f"/api/v1/raphael/conversations/{conversation_id}/messages"
        try:
            resp = await self._client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return str(data["assistant_message"]["content"])
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:300]
            raise RuntimeError(
                f"Raphael API error {exc.response.status_code}: {body}"
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError(
                f"Could not reach Personal OS API at {self.base_url}: {exc}"
            ) from exc

    async def aclose(self) -> None:
        await self._client.aclose()


@dataclass
class TurnTimer:
    """Tracks latency metrics and generates deterministic client_message_ids."""

    _turns: list[dict[str, Any]] = field(default_factory=list)
    _turn_start: float | None = None
    _turn_counter: int = 0

    def next_client_message_id(self, room_name: str) -> str:
        self._turn_counter += 1
        return f"voice-{room_name}-{self._turn_counter}"

    def utterance_end(self) -> None:
        self._turn_start = time.monotonic()

    def first_audio(self) -> None:
        if self._turn_start is not None:
            elapsed_ms = (time.monotonic() - self._turn_start) * 1000
            self._turns.append({
                "ts": datetime.now(UTC).isoformat(),
                "ttfa_ms": round(elapsed_ms, 1),
            })
            log.info("⏱ Time-to-first-audio (TTFA): %.0f ms", elapsed_ms)
            self._turn_start = None

    def report(self) -> str:
        if not self._turns:
            return "No turns recorded."
        ttfas = [t["ttfa_ms"] for t in self._turns]
        return (
            f"Turns: {len(self._turns)} | Avg TTFA: {sum(ttfas)/len(ttfas):.0f} ms | "
            f"Min: {min(ttfas):.0f} ms | Max: {max(ttfas):.0f} ms"
        )


class RaphaelLLM(lk_llm.LLM):
    """LLM adapter delegating conversation turns to RaphaelService via HTTP."""

    def __init__(
        self,
        pos_client: PersonalOSVoiceClient,
        conversation_id: str,
        timer: TurnTimer,
        room_name: str,
    ) -> None:
        super().__init__()
        self._pos = pos_client
        self._conversation_id = conversation_id
        self._timer = timer
        self._room_name = room_name

    def chat(
        self,
        *,
        chat_ctx: lk_llm.ChatContext,
        tools: list[lk_llm.Tool] | None = None,
        conn_options: APIConnectOptions | None = None,
        **kwargs: Any,
    ) -> lk_llm.LLMStream:
        opts = conn_options or APIConnectOptions()
        return _RaphaelLLMStream(
            llm=self,
            chat_ctx=chat_ctx,
            tools=tools or [],
            conn_options=opts,
            pos_client=self._pos,
            conversation_id=self._conversation_id,
            timer=self._timer,
            room_name=self._room_name,
        )


class _RaphaelLLMStream(lk_llm.LLMStream):
    """Streams the Raphael response to the TTS pipeline."""

    def __init__(
        self,
        *,
        llm: RaphaelLLM,
        chat_ctx: lk_llm.ChatContext,
        tools: list[lk_llm.Tool],
        conn_options: APIConnectOptions,
        pos_client: PersonalOSVoiceClient,
        conversation_id: str,
        timer: TurnTimer,
        room_name: str,
    ) -> None:
        super().__init__(llm=llm, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._pos = pos_client
        self._conversation_id = conversation_id
        self._timer = timer
        self._room_name = room_name

    async def _run(self) -> None:
        user_content = ""
        raw_msgs = self._chat_ctx.messages() if callable(self._chat_ctx.messages) else self._chat_ctx.messages
        for msg in reversed(list(raw_msgs)):
            if getattr(msg, "role", None) == "user":
                content = getattr(msg, "content", "")
                if isinstance(content, str):
                    user_content = content
                elif isinstance(content, list):
                    texts = [c for c in content if isinstance(c, str)]
                    user_content = " ".join(texts)
                break

        if not user_content:
            log.warning("No user speech found in chat context; skipping Raphael turn.")
            self._event_ch.send_nowait(
                lk_llm.ChatChunk(
                    id=str(uuid.uuid4()),
                    delta=lk_llm.ChoiceDelta(role="assistant", content="I didn't catch that."),
                )
            )
            return

        client_message_id = self._timer.next_client_message_id(self._room_name)
        log.info("Dispatched spoken turn %s: %s", client_message_id, user_content[:80])

        try:
            raw_reply = await self._pos.send_message(
                conversation_id=self._conversation_id,
                content=user_content,
                client_message_id=client_message_id,
            )
            reply = clean_spoken_text(raw_reply)
            if not reply:
                reply = "I've processed that for you."
        except RuntimeError as exc:
            log.error("Raphael backend invocation error: %s", exc)
            reply = (
                "I'm having trouble connecting to Personal OS right now. "
                "Please verify that the backend API is running."
            )

        log.info("Raphael response (%d chars): %s", len(reply), reply[:100])

        self._event_ch.send_nowait(
            lk_llm.ChatChunk(
                id=client_message_id,
                delta=lk_llm.ChoiceDelta(role="assistant", content=reply),
            )
        )


def _build_speech_pipeline(settings: Settings) -> tuple[Any, Any, Any]:
    """Resolve STT, TTS, and VAD plugins based on available configuration."""
    from livekit.plugins import silero

    vad = silero.VAD.load(min_silence_duration=0.8, min_speech_duration=0.1)

    deepgram_key = os.getenv("DEEPGRAM_API_KEY")
    cartesia_key = os.getenv("CARTESIA_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    # STT Provider resolution
    stt: Any = None
    if deepgram_key:
        try:
            from livekit.plugins import deepgram
            stt = deepgram.STT(api_key=deepgram_key)
            log.info("Using Deepgram STT provider")
        except Exception as exc:
            log.warning("Could not initialize Deepgram STT: %s", exc)

    if stt is None and openai_key:
        try:
            from livekit.plugins import openai as lk_openai
            stt = lk_openai.STT(api_key=openai_key)
            log.info("Using OpenAI Whisper STT provider")
        except Exception as exc:
            log.warning("Could not initialize OpenAI STT: %s", exc)

    if stt is None:
        raise RuntimeError(
            "No STT provider configured. Set DEEPGRAM_API_KEY or OPENAI_API_KEY."
        )

    # TTS Provider resolution
    tts: Any = None
    if cartesia_key:
        try:
            from livekit.plugins import cartesia
            tts = cartesia.TTS(api_key=cartesia_key)
            log.info("Using Cartesia TTS provider")
        except Exception as exc:
            log.warning("Could not initialize Cartesia TTS: %s", exc)

    if tts is None and openai_key:
        try:
            from livekit.plugins import openai as lk_openai
            tts = lk_openai.TTS(api_key=openai_key)
            log.info("Using OpenAI TTS provider")
        except Exception as exc:
            log.warning("Could not initialize OpenAI TTS: %s", exc)

    if tts is None:
        raise RuntimeError(
            "No TTS provider configured. Set CARTESIA_API_KEY or OPENAI_API_KEY."
        )

    return vad, stt, tts


async def entrypoint(ctx: JobContext) -> None:
    """LiveKit agent worker room assignment entrypoint."""
    settings = get_settings()
    log.info("Voice worker assigned to room: %s", ctx.room.name)
    await ctx.connect()
    log.info("Successfully connected to room: %s", ctx.room.name)

    room_meta: dict[str, Any] = {}
    if ctx.room.metadata:
        try:
            room_meta = json.loads(ctx.room.metadata)
        except (json.JSONDecodeError, TypeError) as exc:
            log.warning("Failed to parse room metadata: %s", exc)

    if not room_meta and hasattr(ctx.job, "participant") and ctx.job.participant and ctx.job.participant.metadata:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            room_meta = json.loads(ctx.job.participant.metadata)

    if not room_meta.get("worker_token"):
        for p in ctx.room.remote_participants.values():
            if p.metadata:
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    p_meta = json.loads(p.metadata)
                    if isinstance(p_meta, dict):
                        room_meta.update(p_meta)
                        break

    if not room_meta.get("worker_token"):
        try:
            participant = await ctx.wait_for_participant()
            if participant.metadata:
                p_meta = json.loads(participant.metadata)
                if isinstance(p_meta, dict):
                    room_meta.update(p_meta)
        except Exception as exc:
            log.warning("Could not read participant metadata: %s", exc)

    conversation_id = (
        room_meta.get("conversation_id")
        or (ctx.room.name[10:] if ctx.room.name.startswith("pos-voice-") else None)
        or os.getenv("CONVERSATION_ID")
    )
    if not conversation_id:
        log.error("No conversation_id provided in room/participant metadata or room name")
        return

    worker_token = (
        room_meta.get("worker_token")
        or settings.voice_worker_token
        or settings.dev_auth_token
        or os.getenv("WORKER_API_TOKEN")
        or ""
    )

    pos_base_url = os.getenv("POS_API_BASE_URL", "http://127.0.0.1:8000")
    pos_client = PersonalOSVoiceClient(base_url=pos_base_url, api_token=worker_token)
    timer = TurnTimer()

    vad, stt, tts = _build_speech_pipeline(settings)
    raphael_llm = RaphaelLLM(
        pos_client=pos_client,
        conversation_id=conversation_id,
        timer=timer,
        room_name=ctx.room.name,
    )

    session: AgentSession = AgentSession(
        vad=vad,
        stt=stt,
        llm=raphael_llm,
        tts=tts,
        allow_interruptions=True,
    )

    @session.on("user_input_transcribed")
    def _on_user_input(event: Any) -> None:
        transcript = getattr(event, "transcript", "")
        log.info("🎤 User spoken transcript: %s", transcript)
        timer.utterance_end()

    @session.on("agent_state_changed")
    def _on_agent_state(state: Any) -> None:
        if str(state) == "speaking":
            timer.first_audio()

    agent = Agent(
        instructions=(
            "You are Raphael, an AI assistant in Personal OS. "
            "Your replies are generated by the Personal OS backend service. "
            "Speak naturally and concisely."
        ),
    )

    log.info("Starting Raphael voice agent session in room %s", ctx.room.name)
    await session.start(room=ctx.room, agent=agent)
    await session.generate_reply(
        instructions="Greet the user in one short sentence and let them know Raphael is listening."
    )

    disconnect_event = asyncio.Event()

    @ctx.room.on("disconnected")
    def _on_disconnected() -> None:
        log.info("Room disconnected: %s", ctx.room.name)
        disconnect_event.set()

    @ctx.room.on("participant_disconnected")
    def _on_participant_disconnected(participant: Any) -> None:
        log.info("Participant disconnected: %s", getattr(participant, "identity", participant))
        if len(ctx.room.remote_participants) == 0:
            log.info("All remote participants left room %s; ending session", ctx.room.name)
            disconnect_event.set()

    async def _on_shutdown(reason: str = "") -> None:
        log.info("Worker shutdown triggered: %s", reason)
        disconnect_event.set()

    ctx.add_shutdown_callback(_on_shutdown)

    try:
        await disconnect_event.wait()
    finally:
        log.info("Voice session finished in %s. Metrics: %s", ctx.room.name, timer.report())
        await pos_client.aclose()


def create_voice_agent_worker() -> None:
    """Start the LiveKit agent worker loop."""
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    url = settings.livekit_url
    api_key = settings.livekit_api_key or ("devkey" if settings.environment in ("local", "test") else None)
    api_secret = settings.livekit_api_secret or ("secret" if settings.environment in ("local", "test") else None)

    if not url or not api_key or not api_secret:
        log.error("LiveKit configuration missing. Set LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET.")
        raise SystemExit(1)

    os.environ["LIVEKIT_URL"] = url
    os.environ["LIVEKIT_API_KEY"] = api_key
    os.environ["LIVEKIT_API_SECRET"] = api_secret

    log.info("Launching Personal OS LiveKit Voice Worker (Server: %s)", url)
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


if __name__ == "__main__":
    create_voice_agent_worker()
