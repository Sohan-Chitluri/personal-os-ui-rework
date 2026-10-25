"""Regression tests for Raphael multi-action execution in a single response."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.config import Settings
from personal_os.db.models import User
from personal_os.services.hermes import MockHermesClient
from personal_os.services.raphael import RaphaelService


@pytest.mark.asyncio
async def test_two_task_actions_in_one_response(db_session: AsyncSession, test_user: User):
    """Both task actions are executed, persisted, and confirmed in response."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)

    conv = await service.create_conversation(owner_id=test_user.id, title="Multi-task test")

    reply = (
        "I'll schedule both tasks for you:\n"
        "<action>{\"action\": \"create_task\", \"title\": \"Buy groceries\", \"due_date\": \"tomorrow\"}</action>\n"
        "<action>{\"action\": \"create_task\", \"title\": \"Call dentist\", \"due_date\": \"Friday\"}</action>\n"
        "Let me know if you need anything else!"
    )

    processed = await service._process_task_actions(
        owner_id=test_user.id,
        conversation_id=conv.id,
        reply=reply,
        client_message_id="multi-task-msg-1",
    )

    assert "I have created the task **Buy groceries**" in processed
    assert "I have created the task **Call dentist**" in processed
    assert "Let me know if you need anything else!" in processed

    tasks = await service.task_service.list_tasks(owner_id=test_user.id)
    titles = [t.title for t in tasks]
    assert "Buy groceries" in titles
    assert "Call dentist" in titles


@pytest.mark.asyncio
async def test_project_creation_followed_by_task_creation(db_session: AsyncSession, test_user: User):
    """A project is created first, and the subsequent task action can associate with it."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)

    conv = await service.create_conversation(owner_id=test_user.id, title="Project and Task")

    reply = (
        "Setting up your project and first milestone:\n"
        "```action\n"
        '{"action": "create_project", "name": "Apollo", "description": "Lunar program"}\n'
        "```\n"
        "```action\n"
        '{"action": "create_task", "title": "Build rocket engine", "project": "Apollo"}\n'
        "```"
    )

    processed = await service._process_task_actions(
        owner_id=test_user.id,
        conversation_id=conv.id,
        reply=reply,
        client_message_id="proj-task-turn-1",
    )

    assert "I have created the project **Apollo**." in processed
    assert "I have created the task **Build rocket engine**" in processed

    projects = await service.project_service.list_projects(owner_id=test_user.id)
    assert any(p.name == "Apollo" for p in projects)

    tasks = await service.task_service.list_tasks(owner_id=test_user.id)
    apollo_tasks = [t for t in tasks if t.title == "Build rocket engine"]
    assert len(apollo_tasks) == 1


@pytest.mark.asyncio
async def test_failed_action_followed_by_valid_action(db_session: AsyncSession, test_user: User):
    """An invalid/failed first action reports error without suppressing subsequent valid action."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)

    conv = await service.create_conversation(owner_id=test_user.id, title="Fail and Succeed")

    reply = (
        "<action>{\"action\": \"create_task\", \"title\": \"\"}</action>\n"
        "<action>{\"action\": \"create_task\", \"title\": \"Valid follow-up task\"}</action>"
    )

    processed = await service._process_task_actions(
        owner_id=test_user.id,
        conversation_id=conv.id,
        reply=reply,
        client_message_id="fail-success-msg",
    )

    assert "task title was empty" in processed.lower()
    assert "I have created the task **Valid follow-up task**" in processed

    tasks = await service.task_service.list_tasks(owner_id=test_user.id)
    assert any(t.title == "Valid follow-up task" for t in tasks)


@pytest.mark.asyncio
async def test_duplicate_action_blocks(db_session: AsyncSession, test_user: User):
    """Duplicate identical action blocks in the same turn are deduplicated."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)

    conv = await service.create_conversation(owner_id=test_user.id, title="Deduplication")

    reply = (
        "<action>{\"action\": \"create_task\", \"title\": \"Unique Task 101\"}</action>\n"
        "<action>{\"action\": \"create_task\", \"title\": \"Unique Task 101\"}</action>"
    )

    processed = await service._process_task_actions(
        owner_id=test_user.id,
        conversation_id=conv.id,
        reply=reply,
        client_message_id="dedupe-msg",
    )

    assert "I have created the task **Unique Task 101**" in processed
    assert "Duplicate action ignored" in processed

    tasks = await service.task_service.list_tasks(owner_id=test_user.id)
    matching = [t for t in tasks if t.title == "Unique Task 101"]
    assert len(matching) == 1


@pytest.mark.asyncio
async def test_malformed_and_unauthorized_actions(db_session: AsyncSession, test_user: User):
    """Malformed JSON and unsupported/unauthorized actions are reported clearly."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)

    conv = await service.create_conversation(owner_id=test_user.id, title="Malformed and Unsupported")

    reply = (
        "```action\n"
        '{"action": "create_task", bad_json_syntax}\n'
        "```\n"
        "```action\n"
        '{"action": "drop_database_now"}\n'
        "```\n"
        "<action>{\"action\": \"create_task\", \"title\": \"Surviving Task\"}</action>"
    )

    processed = await service._process_task_actions(
        owner_id=test_user.id,
        conversation_id=conv.id,
        reply=reply,
        client_message_id="malformed-unsupported-msg",
    )

    assert "Action specification was malformed JSON." in processed
    assert "Action 'drop_database_now' is not supported." in processed
    assert "I have created the task **Surviving Task**" in processed

    tasks = await service.task_service.list_tasks(owner_id=test_user.id)
    assert any(t.title == "Surviving Task" for t in tasks)
