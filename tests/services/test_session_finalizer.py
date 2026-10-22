"""Tests for Raphael session lifecycle, 60-minute inactivity finalizer, Diary summarization, and memory recall."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.config import Settings
from personal_os.db.models import Capture, Conversation, ConversationMessage, User
from personal_os.services.session_finalizer import SessionFinalizerService


@pytest.mark.asyncio
async def test_session_finalizer_60_minute_inactivity(db_session: AsyncSession, test_user: User):
    """Conversations inactive for >= 60 minutes are automatically summarized to Diary."""
    settings = Settings(environment="test")
    finalizer = SessionFinalizerService(session=db_session, settings=settings)

    # 1. Create a conversation with last activity 75 minutes ago
    conv = Conversation(
        owner_id=test_user.id,
        title="VLSI Design Session",
        status="active",
        summary_status="active",
        last_activity_at=datetime.now(UTC) - timedelta(minutes=75),
    )
    db_session.add(conv)
    await db_session.commit()
    await db_session.refresh(conv)

    # Add messages
    m1 = ConversationMessage(
        conversation_id=conv.id,
        role="user",
        content="Let's build a CORDIC accelerator in SystemVerilog. We decided to use fixed-point 16-bit.",
    )
    m2 = ConversationMessage(
        conversation_id=conv.id,
        role="assistant",
        content="I have created the task **Implement 16-stage CORDIC pipeline**.\n- Due: None\n- Project: CORDIC",
    )
    m3 = ConversationMessage(
        conversation_id=conv.id,
        role="user",
        content="Next step is to write the testbench in Python.",
    )
    db_session.add_all([m1, m2, m3])
    await db_session.commit()

    # 2. Run finalizer sweep
    summarized_ids = await finalizer.sweep_inactive_conversations(inactivity_minutes=60)
    assert conv.id in summarized_ids

    # 3. Verify conversation status
    await db_session.refresh(conv)
    assert conv.summary_status == "summarized"
    assert conv.summarized_at is not None
    assert conv.summary_capture_id is not None

    # 4. Verify Diary Capture
    cap_stmt = select(Capture).where(Capture.id == conv.summary_capture_id)
    cap_res = await db_session.execute(cap_stmt)
    capture = cap_res.scalar_one()

    assert capture.source == "raphael_summary"
    assert capture.content_type == "session_summary"
    assert "CORDIC" in capture.content
    assert capture.metadata_json is not None
    assert len(capture.metadata_json["tasks"]) == 1
    assert capture.metadata_json["tasks"][0] == "Implement 16-stage CORDIC pipeline"
    assert any("fixed-point" in d for d in capture.metadata_json["decisions"])

    # 5. Search session memory
    memories = await finalizer.search_session_memories(test_user.id, query="CORDIC")
    assert len(memories) >= 1
    assert memories[0]["capture_id"] == capture.id
    assert "CORDIC" in memories[0]["title"]


@pytest.mark.asyncio
async def test_session_finalizer_active_session_not_summarized(db_session: AsyncSession, test_user: User):
    """Conversations active within 60 minutes are not summarized during normal sweeps."""
    settings = Settings(environment="test")
    finalizer = SessionFinalizerService(session=db_session, settings=settings)

    conv = Conversation(
        owner_id=test_user.id,
        title="Active Discussion",
        status="active",
        summary_status="active",
        last_activity_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    db_session.add(conv)
    await db_session.commit()

    m1 = ConversationMessage(conversation_id=conv.id, role="user", content="Hello!")
    db_session.add(m1)
    await db_session.commit()

    summarized_ids = await finalizer.sweep_inactive_conversations(inactivity_minutes=60)
    assert conv.id not in summarized_ids

    await db_session.refresh(conv)
    assert conv.summary_status == "active"
    assert conv.summary_capture_id is None


@pytest.mark.asyncio
async def test_session_finalizer_idempotent(db_session: AsyncSession, test_user: User):
    """Summarizing the same conversation multiple times does not produce duplicate Diary captures."""
    settings = Settings(environment="test")
    finalizer = SessionFinalizerService(session=db_session, settings=settings)

    conv = Conversation(
        owner_id=test_user.id,
        title="Idempotency Test",
        status="active",
        summary_status="active",
        last_activity_at=datetime.now(UTC) - timedelta(minutes=90),
    )
    db_session.add(conv)
    await db_session.commit()

    m1 = ConversationMessage(conversation_id=conv.id, role="user", content="Finalize this session.")
    db_session.add(m1)
    await db_session.commit()

    # First finalization
    c1 = await finalizer.finalize_conversation(conv.id, force=True)
    assert c1 is not None

    # Second finalization
    c2 = await finalizer.finalize_conversation(conv.id, force=True)
    assert c2 is not None
    assert c1.id == c2.id

    # Count captures
    stmt = select(Capture).where(
        Capture.owner_id == test_user.id,
        Capture.idempotency_key == f"raphael-session-{conv.id}",
    )
    res = await db_session.execute(stmt)
    assert len(list(res.scalars().all())) == 1


@pytest.mark.asyncio
async def test_delete_session_memory_soft_deletes_and_preserves_conversation(
    db_session: AsyncSession, test_user: User
):
    """Deleting a session summary soft-deletes the capture, unlinks the conversation, preserves messages, and avoids auto-regeneration."""
    settings = Settings(environment="test")
    finalizer = SessionFinalizerService(session=db_session, settings=settings)

    conv = Conversation(
        owner_id=test_user.id,
        title="Session to Summarize & Delete",
        status="active",
        summary_status="active",
        last_activity_at=datetime.now(UTC) - timedelta(minutes=90),
    )
    db_session.add(conv)
    await db_session.commit()

    m1 = ConversationMessage(
        conversation_id=conv.id,
        role="user",
        content="Important architectural decision about SQLite and Postgres.",
    )
    db_session.add(m1)
    await db_session.commit()

    # 1. Finalize conversation into Diary
    capture = await finalizer.finalize_conversation(conv.id, force=True)
    assert capture is not None
    await db_session.refresh(conv)
    assert conv.summary_capture_id == capture.id
    assert conv.summary_status == "summarized"

    # Memory is searchable
    memories_before = await finalizer.search_session_memories(test_user.id, query="architectural")
    assert len(memories_before) == 1
    assert memories_before[0]["capture_id"] == capture.id

    # 2. Delete the session summary
    success = await finalizer.delete_session_memory(test_user.id, capture.id)
    assert success is True

    # 3. Verify capture is soft-deleted
    await db_session.refresh(capture)
    assert capture.deleted_at is not None

    # 4. Verify conversation is unlinked but conversation and messages still exist
    await db_session.refresh(conv)
    assert conv.summary_capture_id is None
    assert conv.summary_status == "summarized"

    msg_stmt = select(ConversationMessage).where(ConversationMessage.conversation_id == conv.id)
    msg_res = await db_session.execute(msg_stmt)
    messages = list(msg_res.scalars().all())
    assert len(messages) == 1
    assert messages[0].content == "Important architectural decision about SQLite and Postgres."

    # 5. Deleted summary no longer appears in search
    memories_after = await finalizer.search_session_memories(test_user.id, query="architectural")
    assert len(memories_after) == 0

    # 6. Sweeping does NOT regenerate summary
    swept = await finalizer.sweep_inactive_conversations(inactivity_minutes=60)
    assert conv.id not in swept
    await db_session.refresh(conv)
    assert conv.summary_capture_id is None

    # 7. Calling delete again on already deleted memory returns False
    second_delete = await finalizer.delete_session_memory(test_user.id, capture.id)
    assert second_delete is False


@pytest.mark.asyncio
async def test_delete_session_memory_isolation_and_type_check(
    db_session: AsyncSession, test_user: User
):
    """User cannot delete another user's summary and cannot delete non-Raphael captures."""
    settings = Settings(environment="test")
    finalizer = SessionFinalizerService(session=db_session, settings=settings)

    # 1. Normal non-Raphael capture
    manual_capture = Capture(
        owner_id=test_user.id,
        content="Normal manual diary note",
        source="web",
        content_type="text",
        status="processed",
    )
    db_session.add(manual_capture)
    await db_session.commit()
    await db_session.refresh(manual_capture)

    with pytest.raises(ValueError, match="Only Raphael session summaries"):
        await finalizer.delete_session_memory(test_user.id, manual_capture.id)

    # 2. Cross-user isolation
    other_user = User(
        cognito_sub="other-user-sub",
        email="other@example.com",
    )
    db_session.add(other_user)
    await db_session.commit()
    await db_session.refresh(other_user)

    conv = Conversation(
        owner_id=test_user.id,
        title="User 1 Session",
        status="active",
        summary_status="active",
        last_activity_at=datetime.now(UTC) - timedelta(minutes=90),
    )
    db_session.add(conv)
    await db_session.commit()

    m1 = ConversationMessage(
        conversation_id=conv.id,
        role="user",
        content="Important architectural design message.",
    )
    db_session.add(m1)
    await db_session.commit()

    cap = await finalizer.finalize_conversation(conv.id, force=True)
    assert cap is not None

    # Other user attempts to delete user 1's summary -> returns False (not found for other user)
    deleted_by_other = await finalizer.delete_session_memory(other_user.id, cap.id)
    assert deleted_by_other is False

    await db_session.refresh(cap)
    assert cap.deleted_at is None

