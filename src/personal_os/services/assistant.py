"""Bedrock Nova conversational assistant engine for Personal OS (Raphael).

Coordinates conversation turns, server-side tool execution, reference time
injection, and verified task action proposals.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from personal_os.config import Settings
from personal_os.core.llm import (
    ChatMessage,
    ChatResponse,
    LLMProvider,
)
from personal_os.services.assistant_tools import (
    PersonalOSToolExecutor,
    get_personal_os_tools,
)
from personal_os.services.interfaces import ProjectService, TaskService

log = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """\
You are Raphael, the intelligent personal assistant embedded in Personal OS.

CURRENT ENVIRONMENT:
- Reference Application Time: {current_time_iso} ({current_date_str}, {current_weekday})
- Owner ID: {owner_id}

CAPABILITIES & OPERATIONAL RULES:
1. Always use typed tools to inspect the user's workspace:
   - Use `search_tasks` or `get_task` to inspect existing tasks.
   - Use `list_projects` or `get_project` to inspect projects and their context.
   - Use `resolve_relative_date` to calculate exact deadlines (e.g. "tomorrow", "next Friday").
   - Use `propose_task_action` to create, update, reschedule, complete, or delete tasks.
2. Grounded Truth:
   - NEVER invent task IDs, project IDs, or dates.
   - For mutations, first look up the task to get its current revision.
   - Never assume what tasks exist without searching or retrieving them.
3. Safety & Confirmation:
   - For destructive actions (e.g. `delete_task`), requires_confirmation must be True.
   - Clearly explain the rationale and summarize what will change.
4. Tone & Style:
   - Be concise, helpful, and professional.
   - When proposing or executing actions, present clear Markdown summaries.
"""


@dataclass
class AssistantTurnResult:
    reply: str
    action_proposals: list[dict[str, Any]] = field(default_factory=list)
    applied_actions: list[dict[str, Any]] = field(default_factory=list)
    tokens_used: int = 0
    latency_ms: float = 0.0
    model: str = ""


class BedrockAssistantEngine:
    """Orchestrates multi-turn conversation with Amazon Nova 2 Lite and typed application tools."""

    def __init__(
        self,
        provider: LLMProvider,
        settings: Settings,
        max_tool_turns: int = 5,
    ) -> None:
        self.provider = provider
        self.settings = settings
        self.max_tool_turns = max_tool_turns

    async def run_turn(
        self,
        owner_id: str,
        conversation_history: list[ChatMessage],
        new_user_message: str,
        task_service: TaskService,
        project_service: ProjectService,
        ref_time: datetime | None = None,
        auto_apply_safe_proposals: bool = True,
    ) -> AssistantTurnResult:
        """Execute a complete assistant turn with bounded tool-calling loop."""
        ref_time = ref_time or datetime.now(UTC)
        if ref_time.tzinfo is None:
            ref_time = ref_time.replace(tzinfo=UTC)

        executor = PersonalOSToolExecutor(
            owner_id=owner_id,
            task_service=task_service,
            project_service=project_service,
            ref_time=ref_time,
        )

        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            current_time_iso=ref_time.isoformat(),
            current_date_str=ref_time.strftime("%Y-%m-%d"),
            current_weekday=ref_time.strftime("%A"),
            owner_id=owner_id,
        )

        # Clone conversation history and append new user message
        messages: list[ChatMessage] = list(conversation_history)
        messages.append(ChatMessage(role="user", content=new_user_message))

        tools = get_personal_os_tools()

        proposals: list[dict[str, Any]] = []
        applied_results: list[dict[str, Any]] = []
        total_tokens = 0
        total_latency = 0.0
        used_model = ""

        for _turn_idx in range(self.max_tool_turns):
            response: ChatResponse = await self.provider.chat(
                messages=messages,
                tools=tools,
                system_prompt=system_prompt,
                temperature=self.settings.bedrock_temperature,
                max_tokens=self.settings.bedrock_max_tokens,
            )

            total_tokens += response.usage.total_tokens
            total_latency += response.latency_ms
            used_model = response.model

            assistant_msg = response.message
            messages.append(assistant_msg)

            # If no tool calls requested, we reached the final conversational answer
            if not assistant_msg.tool_calls:
                break

            # Execute tool calls requested by the model
            for tc in assistant_msg.tool_calls:
                tool_res = await executor.execute_tool(tc.name, tc.arguments)

                # Capture any action proposals
                is_proposal = (
                    tc.name == "propose_task_action"
                    and tool_res.get("status") == "proposal_generated"
                )
                if is_proposal:
                    proposal_data = tool_res["proposal"]
                    proposals.append(proposal_data)

                    # Auto-apply safe proposals (e.g. single create_task) if requested
                    can_apply = auto_apply_safe_proposals and not proposal_data.get(
                        "requires_confirmation", False
                    )
                    if can_apply:
                        applied = await executor.apply_proposal(proposal_data, confirmed=True)
                        applied_results.append(applied)
                        tool_res["execution_result"] = applied

                # Return tool result to the assistant
                tool_res_str = json.dumps(tool_res)
                messages.append(
                    ChatMessage(
                        role="tool",
                        content=tool_res_str,
                        tool_call_id=tc.id,
                        name=tc.name,
                    )
                )

        final_reply = messages[-1].content if messages else ""

        # If safe proposals were applied, append clean confirmations if not already mentioned
        if applied_results:
            confirmation_lines = []
            for r in applied_results:
                if (
                    r.get("status") == "applied"
                    and r.get("message")
                    and r["message"] not in final_reply
                ):
                    confirmation_lines.append(r["message"])
            if confirmation_lines:
                if final_reply:
                    final_reply = f"{final_reply}\n\n" + "\n".join(confirmation_lines)
                else:
                    final_reply = "\n".join(confirmation_lines)

        return AssistantTurnResult(
            reply=final_reply,
            action_proposals=proposals,
            applied_actions=applied_results,
            tokens_used=total_tokens,
            latency_ms=total_latency,
            model=used_model,
        )
