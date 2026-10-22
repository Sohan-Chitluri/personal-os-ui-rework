"""Live end-to-end smoke test for Raphael with host Hermes CLI and real OpenRouter model.

Validates the full chain:
Live Hermes CLI -> SKILL.md -> Structured Action Protocol -> RaphaelService -> PostgreSQL.
"""

import os
import shutil

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.config import Settings
from personal_os.db.models import User
from personal_os.services.hermes import LiveHermesClient
from personal_os.services.raphael import RaphaelService
from personal_os.services.tasks import TaskServiceImpl


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("OPENROUTER_API_KEY") or not shutil.which("hermes"),
    reason="Live Hermes runtime and OPENROUTER_API_KEY required for live smoke test",
)
async def test_live_hermes_full_lifecycle(db_session: AsyncSession, test_user: User):
    """Executes all 5 core live scenarios end-to-end with the real model and PostgreSQL."""
    settings = Settings(
        environment="test",
        hermes_binary_path=shutil.which("hermes") or "hermes",
        hermes_skill_name="personal-os",
        assistant_backend="hermes",
    )
    live_hermes = LiveHermesClient(
        binary_path=settings.hermes_binary_path,
        default_skill=settings.hermes_skill_name,
    )
    service = RaphaelService(session=db_session, hermes_client=live_hermes, settings=settings)
    task_svc = TaskServiceImpl(db_session)

    conv = await service.create_conversation(owner_id=test_user.id, title="Live AI Smoke Test")

    # --------------------------------------------------------------------------
    # 1. Clear task request with a relative deadline
    # --------------------------------------------------------------------------
    user_msg_1, reply_1 = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Add a task to inspect cloud deployment logs tomorrow",
        client_message_id="live-turn-1",
    )
    assert user_msg_1.content == "Add a task to inspect cloud deployment logs tomorrow"

    tasks_after_1 = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks_after_1) == 1, (
        f"Expected 1 task but got {len(tasks_after_1)}. Reply: {reply_1.content}"
    )
    task_1 = tasks_after_1[0]
    assert "inspect" in task_1.title.lower()
    assert task_1.due_date is not None
    assert str(task_1.id) in reply_1.content

    # --------------------------------------------------------------------------
    # 2. Ambiguous request that should NOT create a task
    # --------------------------------------------------------------------------
    _, reply_2 = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="I need to review our AWS cloud architecture soon",
        client_message_id="live-turn-2",
    )

    tasks_after_2 = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks_after_2) == 1, (
        f"Ambiguous request should not have created a task. Current count: {len(tasks_after_2)}. "
        f"Reply: {reply_2.content}"
    )

    # --------------------------------------------------------------------------
    # 3. Explicit approval of a proposed task
    # --------------------------------------------------------------------------
    _, reply_3 = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Yes, please create a task for that",
        client_message_id="live-turn-3",
    )

    tasks_after_3 = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks_after_3) == 2, (
        f"Approval should have created 1 task (total 2). Current: {len(tasks_after_3)}. "
        f"Reply: {reply_3.content}"
    )
    task_3 = next(t for t in tasks_after_3 if t.id != task_1.id)
    assert str(task_3.id) in reply_3.content

    # --------------------------------------------------------------------------
    # 4. Duplicate submission (Idempotency)
    # --------------------------------------------------------------------------
    dup_user_msg, dup_reply = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Yes, please create a task for that",
        client_message_id="live-turn-3",
    )
    assert dup_reply.id == reply_3.id

    tasks_after_dup = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks_after_dup) == 2, "Duplicate submission must not create duplicate tasks"

    # --------------------------------------------------------------------------
    # 5. Task creation failure that must NOT be reported as success
    # --------------------------------------------------------------------------
    class BrokenTaskService(TaskServiceImpl):
        async def create_task(self, *args, **kwargs):
            raise RuntimeError("Simulated database failure")

    broken_service = RaphaelService(
        session=db_session,
        hermes_client=live_hermes,
        settings=settings,
        task_service=BrokenTaskService(db_session),
    )

    _, fail_reply = await broken_service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Add a task to deploy final production build",
        client_message_id="live-turn-5",
    )

    # Must report failure honestly, not claim success
    assert "failed to create task" in fail_reply.content.lower()
    assert "simulated database failure" in fail_reply.content.lower()
    assert "no task was created" in fail_reply.content.lower()

    tasks_final = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks_final) == 2, "No task should be created upon failure"

    # --------------------------------------------------------------------------
    # 6. Verify API-visible task state in PostgreSQL
    # --------------------------------------------------------------------------
    for t in tasks_final:
        fetched = await task_svc.get_task(test_user.id, t.id)
        assert fetched.id == t.id
        assert fetched.status == "open"
        assert fetched.revision >= 1
