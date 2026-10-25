"""Domain service implementing Raphael conversation and message lifecycle."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.config import Settings
from personal_os.db.models import Conversation, ConversationMessage, Task
from personal_os.services.github import GitHubService
from personal_os.services.hermes import HermesClient, HermesError, strip_hermes_reasoning
from personal_os.services.interfaces import (
    ProjectService,
    ServiceConflictError,
    ServiceNotFound,
    TaskService,
)
from personal_os.services.session_finalizer import SessionFinalizerService
from personal_os.services.tasks import ProjectServiceImpl, TaskServiceImpl

log = logging.getLogger(__name__)

ACTION_FENCE_REGEX = re.compile(
    r"```(?:action|json)?\s*(\{[\s\S]*?\"action\"\s*:\s*\"[a-zA-Z0-9_-]+\"[\s\S]*?\})\s*```"
    r"|<action>\s*(\{[\s\S]*?\"action\"\s*:\s*\"[a-zA-Z0-9_-]+\"[\s\S]*?\})\s*</action>",
    re.IGNORECASE,
)
RAW_JSON_ACTION_REGEX = re.compile(
    r"(\{[^{}]*?\"action\"\s*:\s*\"[a-zA-Z0-9_-]+\"[^{}]*?\})",
    re.IGNORECASE,
)


def _parse_due_date(raw_due: Any, ref_time: datetime | None = None) -> datetime | None:
    """Parse a date or relative indicator into an explicit UTC datetime without inventing dates."""
    if not raw_due or not isinstance(raw_due, str):
        return None
    val = raw_due.strip()
    if val.lower() in ("none", "null", "unset", ""):
        return None

    if ref_time is None:
        ref_time = datetime.now(UTC)
    elif ref_time.tzinfo is None:
        ref_time = ref_time.replace(tzinfo=UTC)

    # Strip soft/hard deadline qualifiers and common prepositions
    clean_val = re.sub(r"(?i)\s*[\(,–-]?\s*(?:soft|hard)\s+deadline\s*[\)]?", "", val).strip()
    clean_val = (
        re.sub(r"(?i)^(?:by|on|before|due(?:\s+date)?(?:\s*:)?)\s+", "", clean_val)
        .strip()
        .rstrip(".! ")
    )

    low = clean_val.lower()
    if low in ("today", "tonight"):
        return ref_time.replace(hour=23, minute=59, second=59, microsecond=0)
    if low == "tomorrow":
        next_day = ref_time + timedelta(days=1)
        return next_day.replace(hour=23, minute=59, second=59, microsecond=0)
    if low == "next week":
        next_week = ref_time + timedelta(days=7)
        return next_week.replace(hour=23, minute=59, second=59, microsecond=0)

    weekdays = (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    )
    if low in weekdays:
        target_wd = weekdays.index(low)
        current_wd = ref_time.weekday()
        days_ahead = (target_wd - current_wd) % 7
        if days_ahead == 0:
            days_ahead = 7
        target_day = ref_time + timedelta(days=days_ahead)
        return target_day.replace(hour=23, minute=59, second=59, microsecond=0)

    # Try ISO formats
    iso_val = clean_val.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(clean_val, fmt).replace(
                hour=23, minute=59, second=59, tzinfo=UTC
            )
        except ValueError:
            pass

    return None


class RaphaelService:
    """Manages conversations, message persistence, runtime turns, and verified task actions."""

    def __init__(
        self,
        session: AsyncSession,
        hermes_client: HermesClient,
        settings: Settings,
        task_service: TaskService | None = None,
        project_service: ProjectService | None = None,
        assistant_engine: Any = None,
        github_service: GitHubService | None = None,
        session_finalizer: SessionFinalizerService | None = None,
    ) -> None:
        self.session = session
        self.hermes = hermes_client
        self.settings = settings
        self.task_service = task_service or TaskServiceImpl(session)
        self.project_service = project_service or ProjectServiceImpl(session)
        self.assistant_engine = assistant_engine
        self.github_service = github_service or GitHubService(session, settings)
        self.session_finalizer = session_finalizer or SessionFinalizerService(session, settings)

    async def get_or_create_active_conversation(self, owner_id: str) -> Conversation:
        """Retrieve the single active conversation, finalizing to Diary if inactive >= 60 minutes."""
        cutoff = datetime.now(UTC) - timedelta(minutes=60)
        stmt = (
            select(Conversation)
            .where(Conversation.owner_id == owner_id)
            .order_by(Conversation.created_at.desc())
            .limit(1)
        )
        res = await self.session.execute(stmt)
        latest = res.scalar_one_or_none()

        if latest is not None:
            if latest.summary_status == "summarized":
                return await self.create_conversation(owner_id)
            elif latest.last_activity_at <= cutoff:
                # Inactive for >= 60 minutes: auto-summarize to Diary
                try:
                    await self.session_finalizer.finalize_conversation(latest.id, force=True)
                except Exception as exc:
                    log.warning(
                        "Could not auto-finalize inactive conversation %s: %s", latest.id, exc
                    )
                return await self.create_conversation(owner_id)
            else:
                return latest

        return await self.create_conversation(owner_id)

    async def reset_active_conversation(self, owner_id: str) -> Conversation:
        """Explicitly finalize any active conversation to Diary and create a fresh one."""
        stmt = (
            select(Conversation)
            .where(
                Conversation.owner_id == owner_id,
                Conversation.summary_status.in_(["active", "pending_summary"]),
            )
            .order_by(Conversation.created_at.desc())
            .limit(1)
        )
        res = await self.session.execute(stmt)
        latest = res.scalar_one_or_none()
        if latest is not None:
            try:
                await self.session_finalizer.finalize_conversation(latest.id, force=True)
            except Exception as exc:
                log.warning("Could not finalize conversation %s on reset: %s", latest.id, exc)

        return await self.create_conversation(owner_id)

    async def finalize_conversation(self, conversation_id: str, force: bool = True) -> Any:
        """Summarize conversation into Diary."""
        return await self.session_finalizer.finalize_conversation(conversation_id, force=force)

    async def search_session_memories(
        self, owner_id: str, query: str = "", limit: int = 5
    ) -> list[dict[str, Any]]:
        """Search Diary summaries from past Raphael sessions."""
        return await self.session_finalizer.search_session_memories(
            owner_id, query=query, limit=limit
        )

    async def delete_session_memory(self, owner_id: str, capture_id: str) -> bool:
        """Soft-delete a Diary session summary."""
        return await self.session_finalizer.delete_session_memory(
            owner_id=owner_id, capture_id=capture_id
        )

    async def list_conversations(
        self,
        owner_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Conversation]:
        """List active conversations for the authenticated owner, newest first."""
        query = (
            select(Conversation)
            .where(Conversation.owner_id == owner_id)
            .order_by(Conversation.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def create_conversation(
        self,
        owner_id: str,
        title: str | None = None,
    ) -> Conversation:
        """Create a new conversation for the authenticated owner."""
        conv = Conversation(
            owner_id=owner_id,
            title=(title or "").strip() or "Conversation with Raphael",
            status="active",
        )
        self.session.add(conv)
        await self.session.commit()
        await self.session.refresh(conv)
        return conv

    async def get_conversation(
        self,
        owner_id: str,
        conversation_id: str,
    ) -> Conversation:
        """Retrieve a specific conversation enforcing owner-scoped isolation."""
        query = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.owner_id == owner_id,
        )
        result = await self.session.execute(query)
        conv = result.scalar_one_or_none()
        if conv is None:
            raise ServiceNotFound(f"Conversation {conversation_id} not found")
        return conv

    async def get_messages(
        self,
        owner_id: str,
        conversation_id: str,
    ) -> list[ConversationMessage]:
        """Retrieve conversation history in chronological order."""
        # Ensure conversation exists and belongs to caller
        await self.get_conversation(owner_id, conversation_id)

        query = (
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at.asc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def send_message(
        self,
        owner_id: str,
        conversation_id: str,
        content: str,
        client_message_id: str | None = None,
    ) -> tuple[ConversationMessage, ConversationMessage]:
        """Persist user message, invoke Hermes session, and persist assistant response.

        Handles duplicate delivery and retries idempotently using client_message_id.
        """
        stripped_content = content.strip()
        if not stripped_content:
            raise ValueError("Message content cannot be blank")

        conversation = await self.get_conversation(owner_id, conversation_id)

        user_msg: ConversationMessage | None = None

        if client_message_id:
            query = select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.client_message_id == client_message_id,
            )
            result = await self.session.execute(query)
            existing_user_msg = result.scalar_one_or_none()

            if existing_user_msg:
                # Check if an assistant reply was already created for this message
                reply_query = (
                    select(ConversationMessage)
                    .where(
                        ConversationMessage.conversation_id == conversation_id,
                        ConversationMessage.role == "assistant",
                        ConversationMessage.created_at >= existing_user_msg.created_at,
                    )
                    .order_by(ConversationMessage.created_at.asc())
                )
                reply_result = await self.session.execute(reply_query)
                existing_reply = reply_result.scalars().first()
                if existing_reply:
                    log.info(
                        "Idempotent replay: message '%s' already answered by '%s'",
                        client_message_id,
                        existing_reply.id,
                    )
                    return existing_user_msg, existing_reply

                # User message was recorded previously, but assistant reply was interrupted
                user_msg = existing_user_msg

        if user_msg is None:
            user_msg = ConversationMessage(
                conversation_id=conversation_id,
                role="user",
                content=stripped_content,
                client_message_id=client_message_id,
            )
            self.session.add(user_msg)
            await self.session.commit()
            await self.session.refresh(user_msg)

        # Update conversation activity timestamp and ensure active
        conversation.last_activity_at = datetime.now(UTC)
        if conversation.summary_status != "active":
            conversation.summary_status = "active"
        self.session.add(conversation)

        # Cross-session Diary recall check
        memory_context = ""
        recall_keywords = (
            "earlier",
            "previous",
            "yesterday",
            "decided",
            "remember",
            "recall",
            "last session",
            "prior meeting",
            "before",
        )
        if any(kw in stripped_content.lower() for kw in recall_keywords):
            try:
                memories = await self.session_finalizer.search_session_memories(
                    owner_id=owner_id,
                    query=stripped_content,
                    limit=2,
                )
                if memories:
                    mem_lines = ["[Diary Memory Context from past sessions:"]
                    for m in memories:
                        mem_lines.append(f"- Session '{m['title']}':")
                        if m.get("decisions"):
                            mem_lines.append(f"  Decisions: {'; '.join(m['decisions'][:3])}")
                        if m.get("next_steps"):
                            mem_lines.append(f"  Next steps: {'; '.join(m['next_steps'][:3])}")
                    mem_lines.append("]")
                    memory_context = "\n".join(mem_lines) + "\n\n"
            except Exception as exc:
                log.debug("Diary recall lookup skipped: %s", exc)

        prompt_to_send = (
            f"{memory_context}{stripped_content}" if memory_context else stripped_content
        )

        use_bedrock = self.assistant_engine is not None or (
            self.settings.assistant_backend == "bedrock"
            and not self.hermes.__class__.__name__.startswith("Mock")
        )

        if use_bedrock:
            if self.assistant_engine is None:
                from personal_os.core.llm import get_provider
                from personal_os.services.assistant import BedrockAssistantEngine

                provider = get_provider(self.settings)
                self.assistant_engine = BedrockAssistantEngine(
                    provider=provider, settings=self.settings
                )

            from personal_os.core.llm import ChatMessage

            past_messages = await self.get_messages(owner_id, conversation_id)
            history = [
                ChatMessage(role=m.role, content=m.content)
                for m in past_messages
                if m.id != user_msg.id
            ]

            turn_result = await self.assistant_engine.run_turn(
                owner_id=owner_id,
                conversation_history=history,
                new_user_message=prompt_to_send,
                task_service=self.task_service,
                project_service=self.project_service,
            )
            effective_reply = turn_result.reply
        else:
            # Invoke Hermes agent runtime
            timeout = self.settings.hermes_timeout_seconds
            session_name = f"pos-{conversation_id}"
            skill = self.settings.hermes_skill_name

            try:
                response = await self.hermes.send_message(
                    prompt=prompt_to_send,
                    session_id=conversation.hermes_session_id,
                    session_name=session_name,
                    skill=skill,
                    timeout_seconds=timeout,
                )
            except HermesError:
                # Let caller/HTTP layer convert into appropriate RFC 7807 problem details
                raise

            # Save Hermes session id if newly assigned
            if response.session_id and conversation.hermes_session_id != response.session_id:
                conversation.hermes_session_id = response.session_id
                self.session.add(conversation)

            # Process any task actions emitted by the agent runtime
            effective_reply = await self._process_task_actions(
                owner_id=owner_id,
                conversation_id=conversation_id,
                reply=response.reply,
                client_message_id=client_message_id,
            )

        # Persist assistant reply
        assistant_msg = ConversationMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=strip_hermes_reasoning(effective_reply),
        )
        self.session.add(assistant_msg)
        await self.session.commit()
        await self.session.refresh(assistant_msg)

        return user_msg, assistant_msg

    async def _find_existing_task(
        self, owner_id: str, client_message_id: str | None
    ) -> Task | None:
        """Find existing task created for client_message_id (idempotency guard)."""
        if not client_message_id:
            return None
        stmt = (
            select(Task)
            .where(
                Task.owner_id == owner_id,
                Task.deleted_at.is_(None),
                Task.extraction_meta["client_message_id"].as_string() == client_message_id,
            )
            .order_by(Task.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _resolve_project(
        self, owner_id: str, raw_project: Any
    ) -> tuple[str | None, str | None, str | None]:
        """Resolves project name or ID against owner's projects.

        Returns (project_id, project_name, note).
        Enforces unambiguous matching: if ambiguous, returns None with note.
        """
        if not raw_project or not isinstance(raw_project, str):
            return None, None, None

        proj_input = raw_project.strip()
        if proj_input.lower() in ("none", "null", "unset", ""):
            return None, None, None

        user_projects = await self.project_service.list_projects(owner_id)

        # 1. Exact ID match
        for p in user_projects:
            if p.id == proj_input:
                return p.id, p.name, None

        # 2. Exact case-insensitive name match
        exact_matches = [p for p in user_projects if p.name.strip().lower() == proj_input.lower()]
        if len(exact_matches) == 1:
            return exact_matches[0].id, exact_matches[0].name, None
        elif len(exact_matches) > 1:
            return (
                None,
                None,
                f"Project was left unset because '{proj_input}' matches multiple projects: "
                + ", ".join(p.name for p in exact_matches),
            )

        # 3. Partial case-insensitive match
        partial_matches = [
            p
            for p in user_projects
            if proj_input.lower() in p.name.lower() or p.name.lower() in proj_input.lower()
        ]
        if len(partial_matches) == 1:
            return partial_matches[0].id, partial_matches[0].name, None
        elif len(partial_matches) > 1:
            return (
                None,
                None,
                f"Project was left unset because '{proj_input}' matches multiple projects: "
                + ", ".join(p.name for p in partial_matches),
            )

        # 4. Not found
        return (
            None,
            None,
            f"Project '{proj_input}' was not found, so the task was created without a project.",
        )

    async def _execute_create_task(
        self,
        owner_id: str,
        conversation_id: str,
        action_data: dict[str, Any],
        client_message_id: str | None,
    ) -> str:
        """Execute task creation with owner scoping, validation, and verified confirmation."""
        title = str(action_data.get("title") or "").strip()
        if not title:
            return "Could not create task: task title was empty."

        raw_due = action_data.get("soft_deadline") or action_data.get("due_date")
        raw_hard = action_data.get("hard_deadline")
        raw_proj = action_data.get("project")
        raw_desc = action_data.get("description")

        description: str | None = None
        if raw_desc and isinstance(raw_desc, str):
            desc_val = raw_desc.strip()
            if desc_val.lower() not in ("none", "null", "unset", ""):
                description = desc_val

        # Preserve soft-deadline qualification in description if indicated and not already present
        is_soft = (
            "soft" in str(raw_due or "").lower()
            or "soft" in str(action_data.get("soft_deadline") or "").lower()
        )
        if is_soft:
            if not description:
                description = "Soft deadline."
            elif "soft deadline" not in description.lower():
                description = f"{description} Soft deadline."

        # 1. Idempotency check: Reuse existing task if this message ID was already processed
        existing_task = await self._find_existing_task(owner_id, client_message_id)
        if existing_task:
            log.info(
                "Idempotent task replay: using existing task '%s' for message '%s'",
                existing_task.id,
                client_message_id,
            )
            due_str = (
                existing_task.due_date.strftime("%Y-%m-%d %H:%M UTC")
                if existing_task.due_date
                else "None"
            )
            hard_deadline = getattr(existing_task, "hard_deadline", None)
            hard_str = (
                hard_deadline.strftime("%Y-%m-%d %H:%M UTC") if hard_deadline is not None else None
            )
            proj_str = "Assigned" if existing_task.project_id else "None"
            lines = [
                f"I have created the task **{existing_task.title}** (ID: `{existing_task.id}`).",
                f"- Due: {due_str}",
            ]
            if hard_str:
                lines.append(f"- Hard deadline: {hard_str}")
            lines.append(f"- Project: {proj_str}")
            if existing_task.description:
                lines.append(f"- Description: {existing_task.description}")
            return "\n".join(lines)

        # 2. Resolve project (enforcing owner-scoping and unambiguous matching)
        project_id, project_name, project_note = await self._resolve_project(owner_id, raw_proj)

        # 3. Parse deadline without inventing dates
        due_date = _parse_due_date(raw_due)
        hard_deadline = _parse_due_date(raw_hard)

        # 4. Create task via TaskService with owner isolation
        task: Task | None = None
        try:
            async with self.session.begin_nested():
                task = await self.task_service.create_task(
                    owner_id=owner_id,
                    title=title,
                    description=description,
                    project_id=project_id,
                    due_date=due_date,
                    hard_deadline=hard_deadline,
                    extraction_meta={
                        "source": "raphael",
                        "conversation_id": conversation_id,
                        "client_message_id": client_message_id,
                    },
                )
        except IntegrityError as exc:
            # Check if this was a concurrent insertion conflict for the same client_message_id
            if client_message_id:
                existing_task = await self._find_existing_task(owner_id, client_message_id)
                if existing_task:
                    log.info(
                        "Concurrent task creation resolved idempotently: "
                        "using existing task '%s' for message '%s'",
                        existing_task.id,
                        client_message_id,
                    )
                    task = existing_task
                else:
                    log.error(
                        "Integrity error during task creation for owner %s: %s",
                        owner_id,
                        exc,
                    )
                    raise
            else:
                log.error(
                    "Integrity error during task creation for owner %s: %s",
                    owner_id,
                    exc,
                )
                raise
        except Exception as exc:
            log.error("Failed to create task for owner %s: %s", owner_id, exc, exc_info=True)
            return f"Failed to create task: {exc}. No task was created."

        due_str = task.due_date.strftime("%Y-%m-%d %H:%M UTC") if task.due_date else "None"
        hard_deadline = getattr(task, "hard_deadline", None)
        hard_str = (
            hard_deadline.strftime("%Y-%m-%d %H:%M UTC") if hard_deadline is not None else None
        )
        proj_str = project_name or "None"
        lines = [
            f"I have created the task **{task.title}** (ID: `{task.id}`).",
            f"- Due: {due_str}",
        ]
        if hard_str:
            lines.append(f"- Hard deadline: {hard_str}")
        lines.append(f"- Project: {proj_str}")
        if task.description:
            lines.append(f"- Description: {task.description}")
        if project_note:
            lines.append(f"- Note: {project_note}")
        return "\n".join(lines)

    async def _execute_create_project(
        self,
        owner_id: str,
        conversation_id: str,
        action_data: dict[str, Any],
        client_message_id: str | None,
    ) -> str:
        """Execute project creation with owner scoping, validation, duplicate handling."""
        raw_name = action_data.get("name")
        name = str(raw_name or "").strip()
        if not name:
            return "Could not create project: project name was empty."

        raw_desc = action_data.get("description")
        description: str | None = None
        if raw_desc and isinstance(raw_desc, str):
            desc_val = raw_desc.strip()
            if desc_val.lower() not in ("none", "null", "unset", ""):
                description = desc_val

        # 1. Check for duplicates among owner's existing projects
        try:
            user_projects = await self.project_service.list_projects(owner_id)
        except Exception as exc:
            log.error("Failed to list projects for owner %s: %s", owner_id, exc, exc_info=True)
            return f"Failed to check existing projects: {exc}. No project was created."

        exact_matches = [p for p in user_projects if p.name.strip().lower() == name.lower()]
        if exact_matches:
            return (
                f"A project named **{exact_matches[0].name}** already exists. "
                "No new project was created."
            )

        # 2. Persist project via ProjectService
        try:
            project = await self.project_service.create_project(
                owner_id=owner_id,
                name=name,
                description=description,
            )
        except ServiceConflictError:
            return f"A project named **{name}** already exists. No new project was created."
        except Exception as exc:
            log.error("Failed to create project for owner %s: %s", owner_id, exc, exc_info=True)
            return f"Failed to create project: {exc}. No project was created."

        # 3. Concise natural-language confirmation without database IDs or raw internal details
        lines = [f"I have created the project **{project.name}**."]
        if project.description:
            lines.append(f"- Description: {project.description}")

        # Check for matching GitHub repositories if connected
        try:
            auth_info = await self.github_service.get_auth_status(owner_id)
            if auth_info.get("connected"):
                matches = await self.github_service.search_repositories(
                    owner_id, query=project.name
                )
                if len(matches) == 1:
                    m = matches[0]
                    lines.append(
                        f"\nI found repository **{m['full_name']}**. "
                        "Would you like to connect this repository to the project?"
                    )
                elif len(matches) > 1:
                    repo_names = ", ".join(f"`{m['full_name']}`" for m in matches[:3])
                    lines.append(
                        f"\nI found multiple matching repositories ({repo_names}). "
                        "Which one would you like to connect?"
                    )
        except Exception as exc:
            log.debug("GitHub discovery check during project creation skipped: %s", exc)

        return "\n".join(lines)

    async def _execute_connect_repository(
        self,
        owner_id: str,
        conversation_id: str,
        action_data: dict[str, Any],
        client_message_id: str | None,
    ) -> str:
        """Execute repository-to-project association with owner validation."""
        raw_proj = action_data.get("project")
        raw_repo = action_data.get("repository")

        if not raw_proj:
            return "Could not connect repository: project name was empty."
        if not raw_repo:
            return "Could not connect repository: repository name was empty."

        project_id, project_name, project_note = await self._resolve_project(owner_id, raw_proj)
        if not project_id:
            return project_note or f"Could not find project '{raw_proj}'."

        try:
            assoc = await self.github_service.associate_project_repository(
                owner_id=owner_id,
                project_id=project_id,
                repo_full_name=str(raw_repo).strip(),
            )
            brief = assoc.project_brief or {}
            purpose = brief.get("purpose") or "Documentation brief and milestones synced."
            return (
                f"Connected repository **{assoc.full_name}** to project **{project_name}**.\n"
                f"- Default branch: `{assoc.default_branch}`\n"
                f"- Brief: {purpose}"
            )
        except ServiceNotFound:
            return f"Project '{raw_proj}' not found."
        except Exception as exc:
            log.warning("Failed to connect repository: %s", exc)
            return f"Failed to connect repository: {exc}."

    async def _process_task_actions(
        self,
        owner_id: str,
        conversation_id: str,
        reply: str,
        client_message_id: str | None,
    ) -> str:
        """Scan reply for action blocks, execute them, and replace with confirmations."""
        sanitized_reply = strip_hermes_reasoning(reply)
        matches = list(ACTION_FENCE_REGEX.finditer(sanitized_reply))
        if not matches:
            matches = list(RAW_JSON_ACTION_REGEX.finditer(sanitized_reply))

        if not matches:
            plain_pattern = (
                r'(?:Task created|Added task|Created task):\s*["\']?([^"\'\n]+?)["\']?'
                r"(?:\s+due\s+([^.\n]+))?[.]?$"
            )
            plain_match = re.search(plain_pattern, sanitized_reply.strip(), re.IGNORECASE)
            if plain_match:
                title = plain_match.group(1).strip()
                raw_due = plain_match.group(2).strip() if plain_match.group(2) else None
                action_data = {
                    "action": "create_task",
                    "title": title,
                    "due_date": raw_due,
                    "project": None,
                }
                return await self._execute_create_task(
                    owner_id=owner_id,
                    conversation_id=conversation_id,
                    action_data=action_data,
                    client_message_id=client_message_id,
                )

            plain_project_pattern = (
                r'(?:Project created|Added project|Created project):\s*["\']?([^"\'\n]+?)["\']?'
                r"(?:\s*[-–:]\s*([^.\n]+))?[.]?$"
            )
            plain_proj_match = re.search(
                plain_project_pattern, sanitized_reply.strip(), re.IGNORECASE
            )
            if plain_proj_match:
                name = plain_proj_match.group(1).strip()
                desc = plain_proj_match.group(2).strip() if plain_proj_match.group(2) else None
                action_data = {
                    "action": "create_project",
                    "name": name,
                    "description": desc,
                }
                return await self._execute_create_project(
                    owner_id=owner_id,
                    conversation_id=conversation_id,
                    action_data=action_data,
                    client_message_id=client_message_id,
                )
            return sanitized_reply

        updated_reply = sanitized_reply
        seen_actions: set[str] = set()

        for action_idx, match in enumerate(matches):
            raw_match = match.group(0)
            json_str = next((g for g in match.groups() if g is not None), None)
            if not json_str:
                continue

            try:
                action_data = json.loads(json_str)
            except json.JSONDecodeError as exc:
                log.warning("Malformed JSON in action block: %s", exc)
                updated_reply = updated_reply.replace(
                    raw_match, "Action specification was malformed JSON.", 1
                )
                continue

            if not isinstance(action_data, dict):
                updated_reply = updated_reply.replace(
                    raw_match, "Action specification was invalid.", 1
                )
                continue

            canonical_key = json.dumps(action_data, sort_keys=True)
            if canonical_key in seen_actions:
                log.info("Deduplicating identical action block in single turn: %s", action_data)
                updated_reply = updated_reply.replace(
                    raw_match, "Duplicate action ignored (already executed in this message).", 1
                )
                continue
            seen_actions.add(canonical_key)

            action_msg_id = (
                client_message_id
                if action_idx == 0
                else f"{client_message_id}:action:{action_idx}"
                if client_message_id
                else None
            )

            action_type = action_data.get("action")
            try:
                if action_type == "create_task":
                    confirmation = await self._execute_create_task(
                        owner_id=owner_id,
                        conversation_id=conversation_id,
                        action_data=action_data,
                        client_message_id=action_msg_id,
                    )
                    updated_reply = updated_reply.replace(raw_match, confirmation, 1).strip()
                elif action_type == "create_project":
                    confirmation = await self._execute_create_project(
                        owner_id=owner_id,
                        conversation_id=conversation_id,
                        action_data=action_data,
                        client_message_id=action_msg_id,
                    )
                    updated_reply = updated_reply.replace(raw_match, confirmation, 1).strip()
                elif action_type == "connect_repository":
                    confirmation = await self._execute_connect_repository(
                        owner_id=owner_id,
                        conversation_id=conversation_id,
                        action_data=action_data,
                        client_message_id=action_msg_id,
                    )
                    updated_reply = updated_reply.replace(raw_match, confirmation, 1).strip()
                else:
                    log.warning("Unsupported action type: %s", action_type)
                    updated_reply = updated_reply.replace(
                        raw_match, f"Action '{action_type}' is not supported.", 1
                    ).strip()
            except Exception as exc:
                log.error("Error executing action %s: %s", action_type, exc, exc_info=True)
                updated_reply = updated_reply.replace(
                    raw_match, f"Action '{action_type}' failed: {exc}", 1
                ).strip()

        return strip_hermes_reasoning(updated_reply)
