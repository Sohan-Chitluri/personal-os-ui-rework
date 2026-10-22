"""Amazon Bedrock LLM provider for Personal OS using Amazon Nova 2 Lite.

Model:            amazon.nova-2-lite-v1:0 (or us.amazon.nova-2-lite-v1:0)
Default Region:   us-east-1
API:              Amazon Bedrock Converse & ConverseStream APIs

Follows the standard AWS SDK credential chain (environment, IAM roles, profiles).
Never hardcodes secrets. Integrates with existing LLMProvider interface, supporting
both semantic extraction and structured conversational tool calling.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from personal_os.core.extraction.models import (
    DeadlineKind,
    ExtractionType,
    RawDeadline,
    RawExtraction,
    RawProjectRef,
)
from personal_os.core.llm import (
    ChatMessage,
    ChatResponse,
    ChatStreamChunk,
    LLMProvider,
    LLMProviderError,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)

log = logging.getLogger(__name__)

_JSON_ARRAY_RE = re.compile(r"\[[\s\S]*\]")

_SYSTEM_PROMPT_EXTRACTION = """\
You are a semantic extraction engine for a personal knowledge management system called Personal OS.

Your task: read a capture (the user's exact words) and extract ALL distinct semantic observations.

CRITICAL RULES:
1. Extract MULTIPLE items from one capture — never collapse a rich capture to a single item.
2. The user's words are preserved elsewhere. You only produce the structured interpretation.
3. Never summarize or replace the user's words. Describe what they mean.
4. Not every temporal mention is a deadline. Preserve it as temporal_context.
5. Preserve uncertainty: "I think..." → certainty≈0.3, not certainty≈1.0.

SEMANTIC TAXONOMY (use exactly these type strings):
  "task"             — committed future work ("I need to", "I must", "I have to")
  "possible_task"    — weak commitment ("I should probably", "Maybe I'll", "I might")
  "activity"         — logged work without explicit progress claim ("I've been working on")
  "progress"         — explicit assertion that something is improved ("The X is now working")
  "problem"          — identified issue without resolution ("X is giving me weird results")
  "decision"         — made choice ("We decided", "Going with", "Chose X over Y")
  "completion"       — explicitly finished something ("I finished X", "Done with X")
  "learning"         — studied or learned something ("I learned", "TIL", "I've been studying")
  "deadline_context" — temporal/contextual mention ("the review is Thursday")
  "observation"      — general note or insight ("I noticed", "Interesting that")
  "journal"          — pure personal reflection with no actionable knowledge

CERTAINTY GUIDE:
  0.0–0.3: speculation, wondering, "I wonder if", "could be", "maybe"
  0.3–0.6: weak intention, "I should probably", "I might", "I think I should"
  0.6–0.8: moderate, "I plan to", "I want to", "I'll try to"
  0.8–1.0: committed, "I need to", "I must", "I will", "I finished", "It is working"

CONTEXT:
{context_block}

OUTPUT: respond with ONLY a valid JSON array. No prose, no markdown fences.
Each item:
{{
  "type": "<type from taxonomy>",
  "description": "<complete, standalone description — do not use pronouns>",
  "project": "<project name as mentioned, or null>",
  "certainty": <0.0–1.0 — USER's expressed commitment, not model confidence>,
  "temporal_context": "<user's temporal expression verbatim, or null>",
  "source_excerpt": "<the phrase that led to this item, or null>",
  "confidence": <0.0–1.0 — your confidence in this extraction>
}}

If the text is noise, a greeting, or has no semantic content: return [].
"""


def calculate_nova_cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate estimated cost in USD based on AWS Bedrock Nova 2 Lite pricing."""
    low_id = model_id.lower()
    if "global" in low_id:
        input_rate = 0.30 / 1_000_000
        output_rate = 2.50 / 1_000_000
    else:
        # Default US regional rate for us.amazon.nova-2-lite-v1:0 / us-east-1
        input_rate = 0.33 / 1_000_000
        output_rate = 2.75 / 1_000_000
    return round((input_tokens * input_rate) + (output_tokens * output_rate), 8)


class BedrockNovaProvider(LLMProvider):
    """Async Amazon Bedrock provider for Nova 2 Lite with Converse API and tool calling."""

    def __init__(
        self,
        model_id: str = "us.amazon.nova-2-lite-v1:0",
        region_name: str = "us-east-1",
        timeout_seconds: float = 30.0,
        max_tokens: int = 4096,
        temperature: float = 0.1,
        top_p: float = 0.9,
        boto3_client: Any = None,
    ) -> None:
        self.model_id = model_id
        self.region_name = region_name
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p
        self._client = boto3_client

    def _get_client(self) -> Any:
        if self._client is None:
            config = Config(
                region_name=self.region_name,
                connect_timeout=10,
                read_timeout=int(self.timeout_seconds),
                retries={"max_attempts": 3, "mode": "standard"},
            )
            session = boto3.Session()
            self._client = session.client("bedrock-runtime", config=config)
        return self._client

    def _format_messages_for_converse(
        self, messages: list[ChatMessage]
    ) -> list[dict[str, Any]]:
        """Format domain ChatMessages into AWS Bedrock Converse format.

        Rules:
        - Only 'user' and 'assistant' roles are allowed in Bedrock Converse messages.
        - Tool results must be represented as a 'user' message with 'toolResult' content blocks.
        - Consecutive messages with the same role are merged to satisfy
          Bedrock role alternating requirements.
        """
        formatted: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == "system":
                # System messages are passed via the 'system' parameter, not messages list
                continue

            content_blocks: list[dict[str, Any]] = []

            if msg.role == "tool":
                # Tool result returned to model
                content_blocks.append({
                    "toolResult": {
                        "toolUseId": msg.tool_call_id or "tool_call_0",
                        "content": [{"text": msg.content}],
                        "status": "success",
                    }
                })
                target_role = "user"
            elif msg.role == "assistant":
                target_role = "assistant"
                if msg.content:
                    content_blocks.append({"text": msg.content})
                for tc in msg.tool_calls:
                    content_blocks.append({
                        "toolUse": {
                            "toolUseId": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        }
                    })
            else:  # user
                target_role = "user"
                if msg.content:
                    content_blocks.append({"text": msg.content})

            if not content_blocks:
                continue

            # Merge with previous message if the role matches
            if formatted and formatted[-1]["role"] == target_role:
                formatted[-1]["content"].extend(content_blocks)
            else:
                formatted.append({"role": target_role, "content": content_blocks})

        return formatted

    def _format_tools_for_converse(
        self, tools: list[ToolDefinition]
    ) -> list[dict[str, Any]]:
        """Format domain ToolDefinitions into Bedrock toolSpec format."""
        bedrock_tools = []
        for t in tools:
            bedrock_tools.append({
                "toolSpec": {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": {
                        "json": t.parameters,
                    },
                }
            })
        return bedrock_tools

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """Execute a conversational completion with optional structured tool calling."""
        client = self._get_client()

        converse_messages = self._format_messages_for_converse(messages)
        if not converse_messages:
            raise LLMProviderError("Cannot call Bedrock Converse with empty messages")

        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        temperature = kwargs.get("temperature", self.temperature)
        top_p = kwargs.get("top_p", self.top_p)

        inference_config: dict[str, Any] = {
            "maxTokens": max_tokens,
            "temperature": temperature,
            "topP": top_p,
        }

        call_kwargs: dict[str, Any] = {
            "modelId": self.model_id,
            "messages": converse_messages,
            "inferenceConfig": inference_config,
        }

        if system_prompt:
            call_kwargs["system"] = [{"text": system_prompt}]

        if tools:
            call_kwargs["toolConfig"] = {
                "tools": self._format_tools_for_converse(tools),
            }

        start_time = time.perf_counter()

        def _invoke() -> dict[str, Any]:
            return client.converse(**call_kwargs)  # type: ignore[no-any-return]

        try:
            response = await asyncio.to_thread(_invoke)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "ClientError")
            msg = exc.response.get("Error", {}).get("Message", str(exc))
            log.error("Bedrock Converse failed (%s): %s", code, msg)
            raise LLMProviderError(f"Bedrock Converse error [{code}]: {msg}") from exc
        except (BotoCoreError, Exception) as exc:
            log.error("Bedrock runtime request error: %s", exc)
            raise LLMProviderError(f"Bedrock runtime request failed: {exc}") from exc

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        output_msg = response.get("output", {}).get("message", {})
        stop_reason = response.get("stopReason", "end_turn")
        usage_dict = response.get("usage", {})

        usage = TokenUsage(
            input_tokens=usage_dict.get("inputTokens", 0),
            output_tokens=usage_dict.get("outputTokens", 0),
            total_tokens=usage_dict.get("totalTokens", 0),
        )

        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in output_msg.get("content", []):
            if "text" in block:
                content_parts.append(block["text"])
            elif "toolUse" in block:
                tu = block["toolUse"]
                tool_calls.append(
                    ToolCall(
                        id=tu.get("toolUseId", ""),
                        name=tu.get("name", ""),
                        arguments=tu.get("input", {}),
                    )
                )

        assistant_msg = ChatMessage(
            role="assistant",
            content="\n".join(content_parts).strip(),
            tool_calls=tool_calls,
        )

        cost = calculate_nova_cost(self.model_id, usage.input_tokens, usage.output_tokens)

        return ChatResponse(
            message=assistant_msg,
            stop_reason=stop_reason,
            usage=usage,
            latency_ms=elapsed_ms,
            model=self.model_id,
            estimated_cost_usd=cost,
        )

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatStreamChunk]:
        """Stream conversational chunks for real-time output."""
        client = self._get_client()
        converse_messages = self._format_messages_for_converse(messages)

        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        temperature = kwargs.get("temperature", self.temperature)
        top_p = kwargs.get("top_p", self.top_p)

        call_kwargs: dict[str, Any] = {
            "modelId": self.model_id,
            "messages": converse_messages,
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": temperature,
                "topP": top_p,
            },
        }

        if system_prompt:
            call_kwargs["system"] = [{"text": system_prompt}]

        if tools:
            call_kwargs["toolConfig"] = {
                "tools": self._format_tools_for_converse(tools),
            }

        def _stream_invoke() -> Any:
            return client.converse_stream(**call_kwargs)

        try:
            response = await asyncio.to_thread(_stream_invoke)
        except Exception as exc:
            raise LLMProviderError(f"Bedrock converse_stream failed: {exc}") from exc

        stream = response.get("stream")
        if not stream:
            return

        def _iterate_events() -> list[dict[str, Any]]:
            return list(stream)

        # Retrieve events
        events = await asyncio.to_thread(_iterate_events)

        current_tool_call: dict[str, Any] | None = None

        for event in events:
            if "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta", {})
                if "text" in delta:
                    yield ChatStreamChunk(delta_text=delta["text"])
                elif "toolUse" in delta:
                    yield ChatStreamChunk(delta_tool_call=delta["toolUse"])

            elif "contentBlockStart" in event:
                start = event["contentBlockStart"].get("start", {})
                if "toolUse" in start:
                    current_tool_call = start["toolUse"]
                    yield ChatStreamChunk(delta_tool_call=current_tool_call)

            elif "messageStop" in event:
                stop_reason = event["messageStop"].get("stopReason")
                yield ChatStreamChunk(stop_reason=stop_reason)

            elif "metadata" in event:
                usage_raw = event["metadata"].get("usage", {})
                usage = TokenUsage(
                    input_tokens=usage_raw.get("inputTokens", 0),
                    output_tokens=usage_raw.get("outputTokens", 0),
                    total_tokens=usage_raw.get("totalTokens", 0),
                )
                yield ChatStreamChunk(usage=usage)

    async def extract(
        self,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> list[RawExtraction]:
        """Extract semantic knowledge items from a capture string using Nova 2 Lite."""
        if not text.strip():
            return []

        context_str = json.dumps(context or {}, indent=2)
        system_text = _SYSTEM_PROMPT_EXTRACTION.format(context_block=context_str)

        chat_messages = [
            ChatMessage(role="user", content=f"Process this capture:\n{text}")
        ]

        response = await self.chat(
            messages=chat_messages,
            system_prompt=system_text,
            temperature=0.0,
            max_tokens=2048,
        )

        reply_text = response.message.content.strip()
        if not reply_text:
            return []

        # Parse JSON array from model output
        json_match = _JSON_ARRAY_RE.search(reply_text)
        if not json_match:
            log.warning("No JSON array found in Bedrock Nova extraction: %r", reply_text)
            return []

        try:
            parsed = json.loads(json_match.group(0))
        except json.JSONDecodeError as exc:
            log.warning("Failed to parse JSON array from Nova extraction: %s", exc)
            return []

        if not isinstance(parsed, list):
            return []

        results: list[RawExtraction] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            type_str = item.get("type", "")
            try:
                ext_type = ExtractionType(type_str)
            except ValueError:
                continue

            desc = str(item.get("description") or "").strip()
            if not desc:
                continue

            certainty = float(item.get("certainty", 0.5))
            certainty = max(0.0, min(1.0, certainty))

            confidence = float(item.get("confidence", 0.8))
            confidence = max(0.0, min(1.0, confidence))

            proj_raw = item.get("project")
            project_ref = (
                RawProjectRef(name=proj_raw, confidence=confidence)
                if proj_raw and isinstance(proj_raw, str) and proj_raw.strip()
                else None
            )

            temp_raw = item.get("temporal_context")
            deadline: RawDeadline | None = None
            if ext_type == ExtractionType.TASK and temp_raw and isinstance(temp_raw, str):
                kind = (
                    DeadlineKind.EXPLICIT_DATE
                    if re.match(r"^\d{4}-\d{2}-\d{2}$", temp_raw.strip())
                    else DeadlineKind.RELATIVE_DAY
                )
                deadline = RawDeadline(
                    kind=kind,
                    raw_text=temp_raw.strip(),
                    confidence=confidence,
                )

            source_excerpt = item.get("source_excerpt")

            results.append(
                RawExtraction(
                    type=ext_type,
                    description=desc,
                    certainty=certainty,
                    confidence=confidence,
                    project_ref=project_ref,
                    deadline=deadline,
                    source_excerpt=source_excerpt,
                    temporal_context=temp_raw,
                    metadata={"model": self.model_id, "provider": "bedrock"},
                )
            )

        results.sort(key=lambda x: x.confidence, reverse=True)
        return results

    async def close(self) -> None:
        """Release any client resources."""
        self._client = None
