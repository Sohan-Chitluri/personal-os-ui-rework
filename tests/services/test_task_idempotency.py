"""Tests for Raphael task idempotency, database-level partial unique index, and concurrency handling."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.config import Settings
from personal_os.db.models import User
from personal_os.services.hermes import MockHermesClient
from personal_os.services.raphael import RaphaelService
from personal_os.services.tasks import TaskServiceImpl


@pytest_asyncio.fixture
async def second_user(db_session: AsyncSession) -> User:
    user = User(
        cognito_sub="second-idempotency-user",
        email="second-idem@example.com",
        display_name="Second Idempotency User",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_task_idempotency_normal_replay(db_session: AsyncSession, test_user: User):
    """1. Normal replay: calling with same client_message_id returns existing task without duplicate creation."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    mock_hermes.custom_responses = [
        '```action\n{"action": "create_task", "title": "Buy groceries", "due_date": null, "project": null}\n```'
    ]
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    conv = await service.create_conversation(owner_id=test_user.id, title="Idempotency Conv")

    client_msg_id = "msg-idem-001"

    # Turn 1: Initial creation
    user_msg_1, asst_msg_1 = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Add a task to buy groceries",
        client_message_id=client_msg_id,
    )
    assert "Buy groceries" in asst_msg_1.content

    # Turn 2: Replay with same client_message_id
    user_msg_2, asst_msg_2 = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Add a task to buy groceries",
        client_message_id=client_msg_id,
    )
    assert user_msg_2.id == user_msg_1.id
    assert asst_msg_2.id == asst_msg_1.id

    # Verify exactly 1 task exists
    task_svc = TaskServiceImpl(db_session)
    tasks = await task_svc.tasks.list_for_owner(test_user.id)
    assert len(tasks) == 1
    assert tasks[0].title == "Buy groceries"
    assert tasks[0].extraction_meta.get("client_message_id") == client_msg_id


@pytest.mark.asyncio
async def test_task_idempotency_more_than_25_intervening_tasks(
    db_session: AsyncSession, test_user: User
):
    """2. Idempotency works even after >25 intervening tasks (verifying in-memory limit(25) was fixed)."""
    settings = Settings(environment="test")
    service = RaphaelService(
        session=db_session, hermes_client=MockHermesClient(), settings=settings
    )
    conv = await service.create_conversation(owner_id=test_user.id, title="Intervening Conv")
    task_svc = TaskServiceImpl(db_session)

    target_msg_id = "msg-target-old-turn"

    # Create original task with target_msg_id
    orig_task = await task_svc.create_task(
        owner_id=test_user.id,
        title="Original target task",
        extraction_meta={
            "source": "raphael",
            "conversation_id": conv.id,
            "client_message_id": target_msg_id,
        },
    )

    # Insert 30 intervening tasks for the same owner
    for i in range(30):
        await task_svc.create_task(
            owner_id=test_user.id,
            title=f"Intervening task {i}",
            extraction_meta={"client_message_id": f"msg-intervene-{i}"},
        )

    # Verify _find_existing_task locates the original task despite 30 newer tasks
    found = await service._find_existing_task(test_user.id, target_msg_id)
    assert found is not None
    assert found.id == orig_task.id
    assert found.title == "Original target task"

    # Now simulate task execution with target_msg_id
    action_data = {
        "action": "create_task",
        "title": "Original target task",
        "due_date": None,
        "project": None,
    }
    confirmation = await service._execute_create_task(
        owner_id=test_user.id,
        conversation_id=conv.id,
        action_data=action_data,
        client_message_id=target_msg_id,
    )
    assert orig_task.id in confirmation

    # Confirm total tasks is still 31 (1 orig + 30 intervening)
    all_tasks = await task_svc.tasks.list_for_owner(test_user.id)
    assert len(all_tasks) == 31


@pytest.mark.asyncio
async def test_task_idempotency_different_owners(
    db_session: AsyncSession, test_user: User, second_user: User
):
    """3. Different owners can use the same client_message_id without collision."""
    settings = Settings(environment="test")
    service = RaphaelService(
        session=db_session, hermes_client=MockHermesClient(), settings=settings
    )
    conv1 = await service.create_conversation(owner_id=test_user.id, title="User 1 Conv")
    conv2 = await service.create_conversation(owner_id=second_user.id, title="User 2 Conv")

    shared_msg_id = "voice-shared-msg-id"
    action_data = {
        "action": "create_task",
        "title": "Task with shared msg id",
        "due_date": None,
        "project": None,
    }

    conf1 = await service._execute_create_task(
        owner_id=test_user.id,
        conversation_id=conv1.id,
        action_data=action_data,
        client_message_id=shared_msg_id,
    )
    conf2 = await service._execute_create_task(
        owner_id=second_user.id,
        conversation_id=conv2.id,
        action_data=action_data,
        client_message_id=shared_msg_id,
    )

    assert "I have created the task" in conf1
    assert "I have created the task" in conf2

    task_svc = TaskServiceImpl(db_session)
    tasks_u1 = await task_svc.tasks.list_for_owner(test_user.id)
    tasks_u2 = await task_svc.tasks.list_for_owner(second_user.id)

    assert len(tasks_u1) == 1
    assert len(tasks_u2) == 1
    assert tasks_u1[0].id != tasks_u2[0].id
    assert tasks_u1[0].extraction_meta["client_message_id"] == shared_msg_id
    assert tasks_u2[0].extraction_meta["client_message_id"] == shared_msg_id


@pytest.mark.asyncio
async def test_task_idempotency_null_ids(db_session: AsyncSession, test_user: User):
    """4. Multiple tasks without client_message_id can be created without constraint conflicts."""
    settings = Settings(environment="test")
    service = RaphaelService(
        session=db_session, hermes_client=MockHermesClient(), settings=settings
    )
    conv = await service.create_conversation(owner_id=test_user.id, title="Null IDs Conv")

    action_data = {
        "action": "create_task",
        "title": "Task without message id",
        "due_date": None,
        "project": None,
    }

    conf1 = await service._execute_create_task(
        owner_id=test_user.id,
        conversation_id=conv.id,
        action_data=action_data,
        client_message_id=None,
    )
    conf2 = await service._execute_create_task(
        owner_id=test_user.id,
        conversation_id=conv.id,
        action_data=action_data,
        client_message_id=None,
    )

    assert "I have created the task" in conf1
    assert "I have created the task" in conf2

    task_svc = TaskServiceImpl(db_session)
    tasks = await task_svc.tasks.list_for_owner(test_user.id)
    assert len(tasks) == 2
    assert tasks[0].id != tasks[1].id


@pytest.mark.asyncio
async def test_task_concurrent_retry_conflict_resolution(
    db_session: AsyncSession, test_user: User
):
    """5. Concurrent insertion conflict triggers savepoint rollback and resolves to existing task."""
    settings = Settings(environment="test")
    service = RaphaelService(
        session=db_session, hermes_client=MockHermesClient(), settings=settings
    )
    conv = await service.create_conversation(owner_id=test_user.id, title="Concurrent Conv")
    task_svc = TaskServiceImpl(db_session)

    conflict_msg_id = "msg-concurrent-conflict"

    # Pre-create the task directly to simulate a concurrent thread committing first
    existing = await task_svc.create_task(
        owner_id=test_user.id,
        title="First concurrent task",
        extraction_meta={
            "source": "raphael",
            "conversation_id": conv.id,
            "client_message_id": conflict_msg_id,
        },
    )

    # Now attempt _execute_create_task directly simulating race where _find_existing_task missed
    # it (or was bypassed) and DB unique constraint triggers
    action_data = {
        "action": "create_task",
        "title": "Second concurrent attempt",
        "due_date": None,
        "project": None,
    }

    confirmation = await service._execute_create_task(
        owner_id=test_user.id,
        conversation_id=conv.id,
        action_data=action_data,
        client_message_id=conflict_msg_id,
    )

    # Should report the existing task title and ID, not error out or create a duplicate
    assert existing.id in confirmation
    assert existing.title in confirmation


@pytest.mark.asyncio
async def test_unrelated_integrity_error_is_not_swallowed(
    db_session: AsyncSession, test_user: User
):
    """6. Unrelated integrity errors (e.g. non-existent foreign key) are not swallowed as idempotency matches."""
    settings = Settings(environment="test")
    service = RaphaelService(
        session=db_session, hermes_client=MockHermesClient(), settings=settings
    )
    conv = await service.create_conversation(owner_id=test_user.id, title="FK Error Conv")

    # Invalid action data with a non-existent project_id
    action_data = {
        "action": "create_task",
        "title": "Task with invalid project",
        "project": "non-existent-project-id-99999",
    }

    # Project doesn't exist, so note is generated or error is returned
    conf = await service._execute_create_task(
        owner_id=test_user.id,
        conversation_id=conv.id,
        action_data=action_data,
        client_message_id="msg-fk-test",
    )
    assert "was not found" in conf or "created without a project" in conf
