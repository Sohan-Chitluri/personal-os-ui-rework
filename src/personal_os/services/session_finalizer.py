"""Session lifecycle finalizer and Diary-backed conversation summarizer."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.config import Settings
from personal_os.db.models import Capture, Conversation, ConversationMessage

log = logging.getLogger(__name__)


def generate_session_summary_heuristic(
    messages: list[ConversationMessage],
    fallback_title: str = "Development Discussion",
) -> tuple[str, str, dict[str, Any]]:
    """Produce structured session summary from conversation messages.

    Returns:
        (session_title, markdown_content, metadata_dict)
    """
    if not messages:
        title = fallback_title
        md = f"# Session Summary: {title}\n\n*No messages recorded in this session.*"
        return (
            title,
            md,
            {"topics": [], "decisions": [], "tasks": [], "work_discussed": [], "next_steps": []},
        )

    user_msgs = [m.content.strip() for m in messages if m.role == "user"]
    assistant_msgs = [m.content.strip() for m in messages if m.role == "assistant"]

    # 1. Determine title from first meaningful user message or conversation topic
    first_user = user_msgs[0] if user_msgs else fallback_title
    # Clean leading commands
    cleaned_first = re.sub(
        r"^(please\s+|can\s+you\s+|i\s+want\s+to\s+|create\s+a\s+|add\s+a\s+)",
        "",
        first_user,
        flags=re.IGNORECASE,
    ).strip()
    session_title = (
        (cleaned_first[:60] + "...")
        if len(cleaned_first) > 60
        else (cleaned_first or fallback_title)
    )

    # 2. Extract decisions & key topics
    topics: list[str] = []
    decisions: list[str] = []
    tasks_created: list[str] = []
    work_discussed: list[str] = []
    next_steps: list[str] = []

    for content in assistant_msgs:
        # Detect task creation confirmations
        task_match = re.search(r"I have created the task \*\*([^*]+)\*\*", content)
        if task_match:
            tasks_created.append(task_match.group(1))

        # Detect project creation confirmations
        proj_match = re.search(r"I have created the project \*\*([^*]+)\*\*", content)
        if proj_match:
            decisions.append(f"Created project: {proj_match.group(1)}")

        # Detect repo connections
        repo_match = re.search(r"Connected repository \*\*([^*]+)\*\*", content)
        if repo_match:
            decisions.append(f"Connected repository: {repo_match.group(1)}")

    # Extract user requirements, obligations, work discussed, and intentions
    for content in user_msgs:
        lines = [line.strip("-* ").strip() for line in content.splitlines() if line.strip()]
        for line in lines:
            lower = line.lower()
            if any(k in lower for k in ("decided", "agree", "choose", "will use", "selected")):
                decisions.append(line)
            # Check for work obligations/deliverables discussed (unscheduled if not created as a task)
            elif any(
                k in lower
                for k in (
                    "have to",
                    "need to",
                    "must",
                    "should",
                    "submit",
                    "fix",
                    "investigate",
                    "email",
                    "finish",
                    "prepare",
                    "bom",
                    "deadline",
                )
            ):
                # Check if it was actually created as a task
                is_task_created = any(
                    tc.lower() in lower or lower in tc.lower() for tc in tasks_created
                )
                if not is_task_created:
                    work_discussed.append(line)
            elif any(k in lower for k in ("next step", "todo", "later", "follow up", "tomorrow")):
                next_steps.append(line)
            elif (
                len(line) > 10
                and len(topics) < 5
                and not any(k in lower for k in ("hi", "hello", "thanks", "yes", "no", "ok"))
            ):
                # Add topic candidate
                topics.append(line[:80])

    # Default fallbacks if empty
    if not decisions and assistant_msgs:
        decisions.append("Explored and reviewed project requirements and technical architecture.")
    if not next_steps and not work_discussed:
        next_steps.append("Continue implementation and verify next milestone.")
    if not topics and user_msgs:
        topics.append(session_title)

    # De-duplicate
    topics = list(dict.fromkeys(topics))[:6]
    decisions = list(dict.fromkeys(decisions))[:6]
    tasks_created = list(dict.fromkeys(tasks_created))[:8]
    work_discussed = list(dict.fromkeys(work_discussed))[:8]
    next_steps = list(dict.fromkeys(next_steps))[:6]

    date_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    md_lines = [
        f"# Session Summary: {session_title}",
        f"**Date**: {date_str}",
        "",
        "### Key Topics",
    ]
    for t in topics:
        md_lines.append(f"- {t}")

    md_lines.append("\n### Decisions & Outcomes")
    for d in decisions:
        md_lines.append(f"- {d}")

    if tasks_created:
        md_lines.append("\n### Created Tasks")
        for tc in tasks_created:
            md_lines.append(f"- {tc}")

    if work_discussed:
        md_lines.append("\n### Work Discussed (Unscheduled)")
        for wd in work_discussed:
            md_lines.append(f"- {wd}")

    md_lines.append("\n### Next Steps")
    for ns in next_steps:
        md_lines.append(f"- {ns}")

    markdown_content = "\n".join(md_lines)
    metadata = {
        "topics": topics,
        "decisions": decisions,
        "tasks": tasks_created,
        "work_discussed": work_discussed,
        "next_steps": next_steps,
    }

    return session_title, markdown_content, metadata


class SessionFinalizerService:
    """Manages 60-minute inactivity session sweeping, Diary capture generation, and memory recall."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def finalize_conversation(
        self,
        conversation_id: str,
        force: bool = False,
    ) -> Capture | None:
        """Summarize conversation into Diary if inactive or forced."""
        stmt = select(Conversation).where(Conversation.id == conversation_id)
        result = await self.session.execute(stmt)
        conv = result.scalar_one_or_none()
        if conv is None:
            return None

        # Already summarized check
        if conv.summary_status == "summarized" and conv.summary_capture_id:
            cap_stmt = select(Capture).where(Capture.id == conv.summary_capture_id)
            cap_res = await self.session.execute(cap_stmt)
            return cap_res.scalar_one_or_none()

        now = datetime.now(UTC)
        inactivity_limit = now - timedelta(minutes=60)

        # Inactivity threshold check unless forced
        if not force and conv.last_activity_at > inactivity_limit:
            log.info(
                "Conversation %s is still active (last activity %s)", conv.id, conv.last_activity_at
            )
            return None

        # Transition to summarizing
        conv.summary_status = "summarizing"
        self.session.add(conv)
        await self.session.commit()

        try:
            # Fetch messages
            msg_stmt = (
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conversation_id)
                .order_by(ConversationMessage.created_at.asc())
            )
            msg_res = await self.session.execute(msg_stmt)
            messages = list(msg_res.scalars().all())

            # Skip creating captures for empty conversations
            if not messages:
                conv.summary_status = "summarized"
                conv.summarized_at = now
                self.session.add(conv)
                await self.session.commit()
                return None

            # Generate structured summary
            title, markdown_content, meta = generate_session_summary_heuristic(
                messages,
                fallback_title=conv.title or "Conversation with Raphael",
            )

            idem_key = f"raphael-session-{conv.id}"

            # Check if capture already exists
            cap_query = select(Capture).where(
                Capture.owner_id == conv.owner_id,
                Capture.idempotency_key == idem_key,
            )
            cap_res = await self.session.execute(cap_query)
            capture = cap_res.scalar_one_or_none()

            meta["conversation_id"] = conv.id
            meta["session_title"] = title

            if capture is None:
                capture = Capture(
                    owner_id=conv.owner_id,
                    content=markdown_content,
                    source="raphael_summary",
                    content_type="session_summary",
                    idempotency_key=idem_key,
                    status="processed",
                    metadata_json=meta,
                )
                self.session.add(capture)
                await self.session.commit()
                await self.session.refresh(capture)

            conv.summary_status = "summarized"
            conv.summarized_at = now
            conv.summary_capture_id = capture.id
            self.session.add(conv)
            await self.session.commit()
            return capture

        except Exception as exc:
            log.error(
                "Failed to summarize conversation %s: %s", conversation_id, exc, exc_info=True
            )
            conv.summary_status = "failed_retryable"
            self.session.add(conv)
            await self.session.commit()
            raise

    async def sweep_inactive_conversations(
        self,
        inactivity_minutes: int = 60,
        batch_size: int = 25,
    ) -> list[str]:
        """Find conversations with > inactivity_minutes elapsed and summarize them."""
        cutoff = datetime.now(UTC) - timedelta(minutes=inactivity_minutes)

        stmt = (
            select(Conversation)
            .where(
                Conversation.last_activity_at <= cutoff,
                Conversation.summary_status.in_(["active", "pending_summary", "failed_retryable"]),
            )
            .order_by(Conversation.last_activity_at.asc())
            .limit(batch_size)
        )
        result = await self.session.execute(stmt)
        candidates = list(result.scalars().all())

        summarized_ids: list[str] = []
        for conv in candidates:
            try:
                await self.finalize_conversation(conv.id, force=True)
                summarized_ids.append(conv.id)
            except Exception as exc:
                log.warning("Sweep failed for conversation %s: %s", conv.id, exc)

        return summarized_ids

    async def search_session_memories(
        self,
        owner_id: str,
        query: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Search past session summaries stored in Diary for the owner."""
        stmt = (
            select(Capture)
            .where(
                Capture.owner_id == owner_id,
                Capture.source == "raphael_summary",
                Capture.deleted_at.is_(None),
            )
            .order_by(Capture.created_at.desc())
            .limit(limit * 2)
        )
        result = await self.session.execute(stmt)
        captures = list(result.scalars().all())

        clean_q = query.strip().lower()
        items: list[dict[str, Any]] = []

        for c in captures:
            content_lower = c.content.lower()
            meta = c.metadata_json or {}
            topics = meta.get("topics", [])
            decisions = meta.get("decisions", [])
            tasks = meta.get("tasks", [])
            work_discussed = meta.get("work_discussed", [])
            next_steps = meta.get("next_steps", [])

            if clean_q:
                # Filter by keyword if provided
                matches = (
                    clean_q in content_lower
                    or any(clean_q in t.lower() for t in topics)
                    or any(clean_q in d.lower() for d in decisions)
                    or any(clean_q in w.lower() for w in work_discussed)
                    or any(clean_q in n.lower() for n in next_steps)
                )
                if not matches:
                    continue

            items.append(
                {
                    "capture_id": c.id,
                    "conversation_id": meta.get("conversation_id"),
                    "title": c.content.splitlines()[0].replace("# Session Summary: ", ""),
                    "summary": c.content,
                    "topics": topics,
                    "decisions": decisions,
                    "tasks": tasks,
                    "work_discussed": work_discussed,
                    "next_steps": next_steps,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                }
            )

            if len(items) >= limit:
                break

        return items

    async def delete_session_memory(
        self,
        owner_id: str,
        capture_id: str,
    ) -> bool:
        """Soft-delete a Diary session summary and unlink from any associated conversation.

        Returns True if deleted, False if not found or already deleted.
        Raises ValueError if the capture is not a Raphael session summary.
        """
        stmt = select(Capture).where(
            Capture.id == capture_id,
            Capture.owner_id == owner_id,
            Capture.deleted_at.is_(None),
        )
        res = await self.session.execute(stmt)
        capture = res.scalar_one_or_none()
        if capture is None:
            return False

        if capture.source != "raphael_summary" and capture.content_type != "session_summary":
            raise ValueError("Only Raphael session summaries can be deleted via this method.")

        now = datetime.now(UTC)
        capture.deleted_at = now
        self.session.add(capture)

        conv_id = None
        if isinstance(capture.metadata_json, dict):
            conv_id = capture.metadata_json.get("conversation_id")

        conv_filters = [Conversation.summary_capture_id == capture.id]
        if conv_id:
            conv_filters.append(Conversation.id == conv_id)

        conv_stmt = select(Conversation).where(
            Conversation.owner_id == owner_id,
            or_(*conv_filters),
        )
        conv_res = await self.session.execute(conv_stmt)
        for conv in conv_res.scalars().all():
            if conv.summary_capture_id == capture.id:
                conv.summary_capture_id = None
            # Keep summary_status as 'summarized' so background sweeps do not re-summarize
            if conv.summary_status != "summarized":
                conv.summary_status = "summarized"
            self.session.add(conv)

        await self.session.commit()
        return True
