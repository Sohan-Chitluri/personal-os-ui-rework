"""Tests for Raphael domain service, message persistence, and session resumption."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from personal_os.config import Settings
from personal_os.db.models import Project, User
from personal_os.services.hermes import HermesTimeoutError, MockHermesClient
from personal_os.services.interfaces import ServiceNotFound
from personal_os.services.raphael import RaphaelService


@pytest_asyncio.fixture
async def second_user(db_session: AsyncSession) -> User:
    user = User(
        cognito_sub="user-two-sub",
        email="user2@example.com",
        display_name="User Two",
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest.mark.asyncio
async def test_create_and_get_conversation(db_session: AsyncSession, test_user: User):
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)

    conv = await service.create_conversation(owner_id=test_user.id, title="My Plan Discussion")
    assert conv.id is not None
    assert conv.owner_id == test_user.id
    assert conv.title == "My Plan Discussion"

    fetched = await service.get_conversation(owner_id=test_user.id, conversation_id=conv.id)
    assert fetched.id == conv.id
    assert fetched.title == "My Plan Discussion"


@pytest.mark.asyncio
async def test_list_conversations(db_session: AsyncSession, test_user: User):
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)

    c1 = await service.create_conversation(owner_id=test_user.id, title="Conv 1")
    c2 = await service.create_conversation(owner_id=test_user.id, title="Conv 2")

    conversations = await service.list_conversations(owner_id=test_user.id)
    ids = [c.id for c in conversations]
    assert c1.id in ids
    assert c2.id in ids
    # Ordered newest first
    assert ids.index(c2.id) < ids.index(c1.id)


@pytest.mark.asyncio
async def test_send_message_persists_user_and_assistant(db_session: AsyncSession, test_user: User):
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    mock_hermes.custom_responses.append("Hello! I am Raphael.")
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)

    conv = await service.create_conversation(owner_id=test_user.id, title="Chat 1")
    user_msg, assistant_msg = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Hello Raphael",
        client_message_id="client-msg-1",
    )

    assert user_msg.role == "user"
    assert user_msg.content == "Hello Raphael"
    assert user_msg.client_message_id == "client-msg-1"
    assert assistant_msg.role == "assistant"
    assert assistant_msg.content == "Hello! I am Raphael."

    messages = await service.get_messages(owner_id=test_user.id, conversation_id=conv.id)
    assert len(messages) == 2
    assert messages[0].id == user_msg.id
    assert messages[1].id == assistant_msg.id


@pytest.mark.asyncio
async def test_duplicate_submission_handling(db_session: AsyncSession, test_user: User):
    """Submitting duplicate client_message_id returns existing messages
    without duplicate calls."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    mock_hermes.custom_responses.append("First response")
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)

    conv = await service.create_conversation(owner_id=test_user.id, title="Idempotency Test")

    # 1. First submission
    u1, a1 = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Check my tasks",
        client_message_id="idem-key-100",
    )
    assert a1.content == "First response"
    assert len(mock_hermes.recorded_calls) == 1

    # 2. Duplicate submission with same client_message_id
    u2, a2 = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Check my tasks",
        client_message_id="idem-key-100",
    )
    assert u2.id == u1.id
    assert a2.id == a1.id
    # Ensure Hermes was NOT called a second time
    assert len(mock_hermes.recorded_calls) == 1

    # History contains only the 2 messages
    history = await service.get_messages(owner_id=test_user.id, conversation_id=conv.id)
    assert len(history) == 2


@pytest.mark.asyncio
async def test_interrupted_message_retry(db_session: AsyncSession, test_user: User):
    """When a prior attempt failed during Hermes execution, retry completes
    the turn without duplicating the user message."""

    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    mock_hermes.simulated_error = HermesTimeoutError("Network dropped")
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)

    conv = await service.create_conversation(owner_id=test_user.id, title="Retry Test")

    # 1. First attempt fails on Hermes
    with pytest.raises(HermesTimeoutError):
        await service.send_message(
            owner_id=test_user.id,
            conversation_id=conv.id,
            content="Heavy request",
            client_message_id="retry-key-200",
        )

    # User message was persisted before Hermes failed
    messages = await service.get_messages(owner_id=test_user.id, conversation_id=conv.id)
    assert len(messages) == 1
    assert messages[0].role == "user"

    # 2. Retry succeeds
    mock_hermes.simulated_error = None
    mock_hermes.custom_responses.append("Recovered response")
    u2, a2 = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Heavy request",
        client_message_id="retry-key-200",
    )
    assert u2.id == messages[0].id
    assert a2.content == "Recovered response"

    final_messages = await service.get_messages(owner_id=test_user.id, conversation_id=conv.id)
    assert len(final_messages) == 2
    assert final_messages[0].id == messages[0].id
    assert final_messages[1].id == a2.id


@pytest.mark.asyncio
async def test_cross_user_isolation(db_session: AsyncSession, test_user: User, second_user: User):
    """User B cannot access or send messages to User A's conversation."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)

    conv_a = await service.create_conversation(owner_id=test_user.id, title="User A Private")

    # User B cannot retrieve User A's conversation
    with pytest.raises(ServiceNotFound):
        await service.get_conversation(owner_id=second_user.id, conversation_id=conv_a.id)

    # User B cannot get messages from User A's conversation
    with pytest.raises(ServiceNotFound):
        await service.get_messages(owner_id=second_user.id, conversation_id=conv_a.id)

    # User B cannot send messages to User A's conversation
    with pytest.raises(ServiceNotFound):
        await service.send_message(
            owner_id=second_user.id,
            conversation_id=conv_a.id,
            content="Intruder message",
        )


@pytest.mark.asyncio
async def test_blank_message_rejected(db_session: AsyncSession, test_user: User):
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    conv = await service.create_conversation(owner_id=test_user.id)

    with pytest.raises(ValueError, match="cannot be blank"):
        await service.send_message(owner_id=test_user.id, conversation_id=conv.id, content="   ")


@pytest.mark.asyncio
async def test_conversation_recovery_after_backend_restart(
    db_session_factory: async_sessionmaker[AsyncSession],
    test_user: User,
):
    """Conversation recovery after backend restart (fresh session and service)."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()

    # Session 1: Create conversation and send turn 1
    async with db_session_factory() as session1:
        service1 = RaphaelService(session=session1, hermes_client=mock_hermes, settings=settings)
        conv = await service1.create_conversation(owner_id=test_user.id, title="Persistent Thread")
        await service1.send_message(
            owner_id=test_user.id,
            conversation_id=conv.id,
            content="Turn 1",
            client_message_id="msg-turn-1",
        )
        conv_id = conv.id

    # Simulate backend restart: fresh session2, fresh service2
    async with db_session_factory() as session2:
        service2 = RaphaelService(session=session2, hermes_client=mock_hermes, settings=settings)
        # Fetch conversation
        resumed_conv = await service2.get_conversation(
            owner_id=test_user.id, conversation_id=conv_id
        )

        assert resumed_conv.title == "Persistent Thread"
        assert resumed_conv.hermes_session_id is not None
        saved_hermes_sid = resumed_conv.hermes_session_id

        # Verify history is intact
        history = await service2.get_messages(owner_id=test_user.id, conversation_id=conv_id)
        assert len(history) == 2

        # Send turn 2
        await service2.send_message(
            owner_id=test_user.id,
            conversation_id=conv_id,
            content="Turn 2",
            client_message_id="msg-turn-2",
        )

        # Hermes received the recorded hermes_session_id
        last_call = mock_hermes.recorded_calls[-1]
        assert last_call["session_id"] == saved_hermes_sid
        assert last_call["prompt"] == "Turn 2"

        history_after = await service2.get_messages(owner_id=test_user.id, conversation_id=conv_id)
        assert len(history_after) == 4


@pytest.mark.asyncio
async def test_raphael_task_action_dual_deadlines(
    db_session: AsyncSession,
    test_user: User,
):
    """Raphael correctly parses soft_deadline and hard_deadline from action blocks."""
    from personal_os.db.models import Task

    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    action_json = (
        '```action\n'
        '{\n'
        '  "action": "create_task",\n'
        '  "title": "Dual Deadline Mission",\n'
        '  "soft_deadline": "2026-11-01T10:00:00Z",\n'
        '  "hard_deadline": "2026-11-05T12:00:00Z"\n'
        '}\n'
        '```'
    )
    mock_hermes.custom_responses = [f"I will schedule that for you.\n{action_json}"]

    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    conv = await service.create_conversation(owner_id=test_user.id, title="Task Planning")

    user_msg, assistant_msg = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Create a mission task with soft deadline Nov 1 and hard deadline Nov 5",
        client_message_id="msg-dual-deadlines",
    )

    assert "I have created the task **Dual Deadline Mission**" in assistant_msg.content
    assert "Due: 2026-11-01 10:00 UTC" in assistant_msg.content
    assert "Hard deadline: 2026-11-05 12:00 UTC" in assistant_msg.content

    # Verify task in DB
    from sqlalchemy import select
    res = await db_session.execute(
        select(Task).where(Task.owner_id == test_user.id, Task.title == "Dual Deadline Mission")
    )
    task = res.scalar_one_or_none()
    assert task is not None
    assert task.due_date is not None
    assert task.hard_deadline is not None


@pytest.mark.asyncio
async def test_process_task_actions_strips_reasoning_and_executes_create_project():
    """Regression test: reasoning blocks are removed and create_project action is executed."""
    from unittest.mock import AsyncMock

    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    mock_session = AsyncMock()

    mock_project_service = AsyncMock()
    mock_project_service.list_projects.return_value = []
    created_proj = Project(
        id="proj-cordic-1",
        owner_id="user-1",
        name="CORDIC",
        description=None,
    )
    mock_project_service.create_project.return_value = created_proj

    service = RaphaelService(
        session=mock_session,
        hermes_client=mock_hermes,
        settings=settings,
        project_service=mock_project_service,
    )

    raw_reply = (
        "┌─ Reasoning ──────────────────────────────────────────────────────────────────┐\n"
        "│ The user wants to add a project named 'cordic' to their project list.         │\n"
        "│ Looking at the available tools and instructions:                             │\n"
        "│ - We can output an action block:                                             │\n"
        "└──────────────────────────────────────────────────────────────────────────────┘\n"
        "```action\n"
        '{"action": "create_project", "name": "CORDIC", "description": null}\n'
        "```\n"
        "Adding the CORDIC project to your list now..."
    )

    processed = await service._process_task_actions(
        owner_id="user-1",
        conversation_id="conv-1",
        reply=raw_reply,
        client_message_id="msg-cordic",
    )

    # 1. Reasoning box must be stripped
    assert "┌─ Reasoning" not in processed
    assert "The user wants to add a project" not in processed

    # 2. Project creation was executed
    mock_project_service.create_project.assert_awaited_once_with(
        owner_id="user-1",
        name="CORDIC",
        description=None,
    )

    # 3. Clean confirmation and reply body are preserved
    assert "I have created the project **CORDIC**." in processed
    assert "Adding the CORDIC project to your list now..." in processed


@pytest.mark.asyncio
async def test_process_task_actions_handles_unsupported_action():
    """Unrecognized action blocks do not crash and report clear unsupported explanation."""
    from unittest.mock import AsyncMock

    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    mock_session = AsyncMock()

    service = RaphaelService(
        session=mock_session,
        hermes_client=mock_hermes,
        settings=settings,
    )

    raw_reply = (
        "```action\n"
        '{"action": "delete_all_projects", "target": "all"}\n'
        "```"
    )

    processed = await service._process_task_actions(
        owner_id="user-1",
        conversation_id="conv-1",
        reply=raw_reply,
        client_message_id="msg-unsupported",
    )

    assert "Action 'delete_all_projects' is not supported." in processed


@pytest.mark.asyncio
async def test_process_task_actions_handles_malformed_json():
    """Malformed JSON action blocks do not crash and indicate malformed specification."""
    from unittest.mock import AsyncMock

    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    mock_session = AsyncMock()

    service = RaphaelService(
        session=mock_session,
        hermes_client=mock_hermes,
        settings=settings,
    )

    raw_reply = (
        "```action\n"
        '{"action": "create_project", "name": CORDIC_WITHOUT_QUOTES}\n'
        "```"
    )

    processed = await service._process_task_actions(
        owner_id="user-1",
        conversation_id="conv-1",
        reply=raw_reply,
        client_message_id="msg-malformed",
    )

    assert "Action specification was malformed JSON." in processed
