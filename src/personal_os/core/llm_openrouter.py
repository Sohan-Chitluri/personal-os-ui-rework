"""OpenRouter LLM provider for semantic extraction.

Runtime model: google/gemma-4-31b-it:free
API key:       $OPENROUTER_API_KEY  (never hardcoded)

The provider calls the LLM and parses the response into RawExtraction objects.
It produces MULTIPLE items per capture — one capture can contain many semantic
observations. No model output reaches the vault directly; it flows through
ActionPlanner to typed VaultActions.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import httpx

from personal_os.core.extraction.models import (
    DeadlineKind,
    ExtractionType,
    RawDeadline,
    RawExtraction,
    RawProjectRef,
)
from personal_os.core.llm import LLMProvider, LLMProviderError

log = logging.getLogger(__name__)

_JSON_ARRAY_RE = re.compile(r'\[.*?\]', re.DOTALL)

# certainty: the USER's expressed commitment/certainty (0.0=speculation, 1.0=definite).
# This is NOT the model's confidence in its own extraction.
# Examples: "I think..." → certainty≈0.3; "I need to..." → certainty≈0.95.
#
# temporal_context: the user's temporal expression preserved LITERALLY.
# "tomorrow", "Thursday", "next week" — NOT normalized to a date here.
# The Resolver normalizes using ExtractionContext.now.
# Not every temporal expression is a deadline.

_SYSTEM_PROMPT = """\
You are a semantic extraction engine for a personal knowledge management system called Personal OS.

Your task: read a capture (the user's exact words) and extract ALL distinct semantic observations.

CRITICAL RULES:
1. Extract MULTIPLE items from one capture — never collapse a rich capture to a single item.
2. The user's words are preserved elsewhere. You only produce the structured interpretation.
3. Never summarize or replace the user's words. Describe what they mean.
4. Not every temporal mention is a deadline. Preserve it as temporal_context.
5. Preserve uncertainty: "I think..." → certainty≈0.3, not certainty≈1.0.

SEMANTIC TAXONOMY (use exactly these type strings):
  "task"             — committed future work. Strong language: "I need to", "I must", "I have to"
  "possible_task"    — weak commitment. "I should probably", "Maybe I'll", "I might", "I could"
  "activity"         — logged work without explicit progress claim. "I've been working on", "I spent time on"
  "progress"         — explicit assertion that something is now working/done/improved. "The X is now working", "X is fixed"
  "problem"          — identified issue without resolution. "X is giving me weird results", "X is broken"
  "decision"         — made choice. "We decided", "Going with", "Chose X over Y"
  "completion"       — explicitly finished something. "I finished X", "Done with X", "Completed X"
  "learning"         — studied or learned something. "I learned", "TIL", "I've been studying"
  "deadline_context" — temporal/contextual mention with no committed task. "the review is Thursday", "class is Monday"
  "observation"      — general note or insight. "I noticed", "Interesting that"
  "journal"          — pure personal reflection with no actionable knowledge

EXAMPLE — RICH CAPTURE (produces multiple items, not one):

Capture: "I've been working on the CORDIC pipeline most of the evening. The fixed-point stage is mostly working now, but the scaling is still giving me weird results. I think I need to revisit that tomorrow because the review is on Thursday."

Correct output:
[
  {"type": "activity", "description": "Working on CORDIC pipeline", "project": "CORDIC", "certainty": 0.95, "temporal_context": null, "source_excerpt": "I've been working on the CORDIC pipeline most of the evening"},
  {"type": "progress", "description": "Fixed-point stage mostly working", "project": "CORDIC", "certainty": 0.75, "temporal_context": null, "source_excerpt": "The fixed-point stage is mostly working now"},
  {"type": "problem", "description": "Scaling produces unexpected results", "project": "CORDIC", "certainty": 0.9, "temporal_context": null, "source_excerpt": "the scaling is still giving me weird results"},
  {"type": "possible_task", "description": "Revisit/investigate CORDIC scaling issue", "project": "CORDIC", "certainty": 0.4, "temporal_context": "tomorrow", "source_excerpt": "I think I need to revisit that tomorrow"},
  {"type": "deadline_context", "description": "CORDIC review", "project": "CORDIC", "certainty": 0.9, "temporal_context": "Thursday", "source_excerpt": "the review is on Thursday"}
]

WRONG output (too lossy — collapses everything):
[{"type": "task", "description": "Fix CORDIC scaling", "project": "CORDIC", "certainty": 0.8}]

WEAK INTENTION EXAMPLE:
Capture: "I should probably clean up the CORDIC assertions sometime."
→ type = "possible_task", certainty = 0.25 — NOT "task"

STRONG TASK EXAMPLE:
Capture: "I need to finish the CAD work for IC Design tomorrow."
→ type = "task", certainty = 0.95, temporal_context = "tomorrow"

CERTAINTY GUIDE:
  0.0–0.3: speculation, wondering, "I wonder if", "could be", "maybe"
  0.3–0.6: weak intention, "I should probably", "I might", "I think I should"
  0.6–0.8: moderate, "I plan to", "I want to", "I'll try to"
  0.8–1.0: committed, "I need to", "I must", "I will", "I finished", "It is working"

CONTEXT (use to resolve implicit references like "that problem" or "it"):
{context_block}

OUTPUT: respond with ONLY a valid JSON array. No prose, no markdown fences.
Each item:
{
  "type": "<type from taxonomy>",
  "description": "<complete, standalone description — do not use pronouns>",
  "project": "<project name as mentioned, or null>",
  "certainty": <0.0–1.0 — USER's expressed commitment, not model confidence>,
  "temporal_context": "<user's temporal expression verbatim, or null>",
  "source_excerpt": "<the phrase that led to this item, or null>",
  "confidence": <0.0–1.0 — your confidence in this extraction>
}

If the text is noise, a greeting, or has no semantic content: return [].
"""


class OpenRouterProvider(LLMProvider):
    """Async OpenRouter client for multi-item semantic extraction via Gemma 4 31B."""

    _TIMEOUT = httpx.Timeout(45.0, connect=10.0)

    def __init__(
        self,
        model: str = "google/gemma-4-31b-it:free",
        base_url: str = "https://openrouter.ai/api/v1",
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ public

    async def extract(
        self,
        text: str,
        context: dict | None = None,
    ) -> list[RawExtraction]:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise LLMProviderError("OPENROUTER_API_KEY is not set")

        system_prompt = _SYSTEM_PROMPT.replace(
            "{context_block}", self._format_context(context or {})
        )

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text.strip()},
            ],
            "temperature": 0.1,
            "max_tokens": 1024,
        }

        client = await self._get_client(api_key)
        t0 = time.monotonic()
        try:
            resp = await client.post(f"{self._base_url}/chat/completions", json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LLMProviderError(
                f"OpenRouter HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except httpx.RequestError as exc:
            raise LLMProviderError(f"OpenRouter request failed: {exc}") from exc

        latency_ms = int((time.monotonic() - t0) * 1000)
        data = resp.json()
        raw_content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        tokens = data.get("usage", {})

        log.debug("OpenRouter extract: model=%s latency_ms=%d tokens=%s", self._model, latency_ms, tokens)
        return self._parse(raw_content, latency_ms=latency_ms, tokens=tokens)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ private

    async def _get_client(self, api_key: str) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/personal-os",
                    "X-Title": "Personal OS",
                },
                timeout=self._TIMEOUT,
            )
        return self._client

    @staticmethod
    def _format_context(context: dict) -> str:
        parts = []
        if context.get("current_date"):
            parts.append(f"Today is {context['current_date']} ({context.get('current_day_of_week', '')}), timezone: {context.get('timezone', 'UTC')}.")
        if context.get("known_projects"):
            parts.append(f"Known projects: {', '.join(context['known_projects'][:15])}.")
        if context.get("current_project"):
            parts.append(f"Current project context: {context['current_project']}.")
        if context.get("recent_captures"):
            recent = context["recent_captures"][-3:]
            parts.append("Recent captures (for implicit reference resolution):\n" +
                         "\n".join(f"  - {c[:120]}" for c in recent))
        if context.get("active_tasks"):
            tasks = context["active_tasks"][:5]
            parts.append("Active tasks:\n" + "\n".join(f"  - {t}" for t in tasks))
        return "\n".join(parts) if parts else "(no context provided)"

    def _parse(
        self,
        content: str,
        latency_ms: int = 0,
        tokens: dict | None = None,
    ) -> list[RawExtraction]:
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r'^```[a-z]*\n?', '', content)
            content = re.sub(r'\n?```$', '', content)

        m = _JSON_ARRAY_RE.search(content)
        if not m:
            log.warning("OpenRouter returned no JSON array: %r", content[:200])
            return []

        try:
            items: list[dict] = json.loads(m.group())
        except json.JSONDecodeError as exc:
            log.warning("OpenRouter JSON parse error: %s | raw=%r", exc, content[:200])
            return []

        if not isinstance(items, list):
            return []

        meta = {"model": self._model, "latency_ms": latency_ms, **(tokens or {})}
        results: list[RawExtraction] = []
        for item in items:
            try:
                results.append(self._item_to_extraction(item, meta))
            except (KeyError, ValueError) as exc:
                log.warning("Skipping malformed extraction item %r: %s", item, exc)
        return results

    def _item_to_extraction(self, item: dict[str, Any], meta: dict[str, Any]) -> RawExtraction:
        type_str = str(item.get("type", "journal")).lower()
        try:
            ext_type = ExtractionType(type_str)
        except ValueError:
            ext_type = ExtractionType.JOURNAL

        description = str(item.get("description", "")).strip()
        if not description:
            raise ValueError("description is empty")

        confidence = max(0.0, min(1.0, float(item.get("confidence", 0.8))))
        certainty = max(0.0, min(1.0, float(item.get("certainty", 1.0))))
        source_excerpt = item.get("source_excerpt") or None
        temporal_context = item.get("temporal_context") or None

        project_ref: RawProjectRef | None = None
        if item.get("project"):
            project_ref = RawProjectRef(
                name=str(item["project"]).strip(),
                confidence=confidence,
                is_implicit=False,
            )

        # Build RawDeadline from temporal_context — only for TASK type.
        # For other types, temporal_context stays as a string field only.
        deadline: RawDeadline | None = None
        if ext_type == ExtractionType.TASK and temporal_context:
            kind = (
                DeadlineKind.EXPLICIT_DATE
                if re.match(r'^\d{4}-\d{2}-\d{2}$', temporal_context)
                else DeadlineKind.RELATIVE_DAY
            )
            deadline = RawDeadline(kind=kind, raw_text=temporal_context, confidence=confidence)

        return RawExtraction(
            type=ext_type,
            description=description,
            project_ref=project_ref,
            deadline=deadline,
            confidence=confidence,
            certainty=certainty,
            source_excerpt=source_excerpt,
            temporal_context=temporal_context,
            metadata=meta,
        )
