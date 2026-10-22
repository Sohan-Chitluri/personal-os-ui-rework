"""Tests for Raphael natural-language task creation, confirmation policies, and sync integration."""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.config import Settings
from personal_os.db.models import Task, User
from personal_os.services.hermes import LiveHermesClient, MockHermesClient
from personal_os.services.interfaces import ServiceNotFound
from personal_os.services.raphael import RaphaelService
from personal_os.services.tasks import ProjectServiceImpl, TaskServiceImpl
from personal_os.sync.postgres_store import SyncChangeEvent


@pytest_asyncio.fixture
async def second_user(db_session: AsyncSession) -> User:
    user = User(
        cognito_sub="second-user-sub",
        email="second-user@example.com",
        display_name="Second User",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_clear_task_request_creates_one_persisted_task(
    db_session: AsyncSession, test_user: User
):
    """1. Clear intent and sufficient fields: creates exactly one persisted task and reports verified ID."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    project_svc = ProjectServiceImpl(db_session)
    task_svc = TaskServiceImpl(db_session)

    # Setup user project
    project = await project_svc.create_project(
        owner_id=test_user.id, name="VLSI", description="VLSI course"
    )

    conv = await service.create_conversation(owner_id=test_user.id, title="Task Creation Demo")

    # Send clear task request
    user_msg, assistant_msg = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Add a task to finish my VLSI assignment tomorrow",
        client_message_id="client-turn-1",
    )

    assert user_msg.content == "Add a task to finish my VLSI assignment tomorrow"

    # Query database for created tasks
    tasks = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks) == 1
    created_task = tasks[0]

    assert created_task.title == "Finish my VLSI assignment"
    assert created_task.project_id == project.id
    assert created_task.due_date is not None
    assert created_task.status == "open"

    # Verify assistant message contains canonical persisted task ID and details
    assert str(created_task.id) in assistant_msg.content
    assert "Finish my VLSI assignment" in assistant_msg.content
    assert "VLSI" in assistant_msg.content


@pytest.mark.asyncio
async def test_ambiguous_request_asks_clarification_without_creating_task(
    db_session: AsyncSession, test_user: User
):
    """2. Ambiguous intent: asks whether user wants a task created without making any mutation."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    task_svc = TaskServiceImpl(db_session)

    conv = await service.create_conversation(owner_id=test_user.id, title="Ambiguous Thought")

    # User expresses ambiguous reflection/need
    _, assistant_msg = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="I need to finish my assignment soon",
        client_message_id="ambiguous-turn-1",
    )

    # Assert no task was created in the database
    tasks = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks) == 0

    # Assert assistant asked a clarifying question rather than executing a mutation
    assert "would you like me to create a task" in assistant_msg.content.lower()


@pytest.mark.asyncio
async def test_missing_deadline_does_not_invent_deadline(
    db_session: AsyncSession, test_user: User
):
    """3. Missing essential information: missing deadline does not result in an invented deadline."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    task_svc = TaskServiceImpl(db_session)

    conv = await service.create_conversation(owner_id=test_user.id, title="No Deadline Task")

    _, assistant_msg = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Add a task to read Chapter 4",
        client_message_id="no-deadline-turn",
    )

    tasks = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.title == "Read Chapter 4"
    # Canonical invariant: deadline must be None, never invented
    assert task.due_date is None
    assert str(task.id) in assistant_msg.content
    assert "Due: None" in assistant_msg.content


@pytest.mark.asyncio
async def test_project_association_exact_and_ambiguous(
    db_session: AsyncSession, test_user: User
):
    """4. Project association is correct or left unset when ambiguous."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    project_svc = ProjectServiceImpl(db_session)
    task_svc = TaskServiceImpl(db_session)

    # User has two projects with "Math" in their names
    p1 = await project_svc.create_project(owner_id=test_user.id, name="Math 101")
    p2 = await project_svc.create_project(owner_id=test_user.id, name="Math 201")

    conv = await service.create_conversation(owner_id=test_user.id, title="Project Ambiguity")

    # Ambiguous project target: "Math" matches both Math 101 and Math 201
    mock_hermes.custom_responses.append(
        '```action\n{"action": "create_task", "title": "Solve problem set", "project": "Math"}\n```'
    )
    _, reply1 = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Add a task to solve problem set for project Math",
    )

    # Task was created with project_id = None because project target was ambiguous
    tasks = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks) == 1
    assert tasks[0].project_id is None
    assert "multiple projects" in reply1.content.lower()

    # Now unambiguous exact match: "Math 101"
    mock_hermes.custom_responses.append(
        f'```action\n{{"action": "create_task", "title": "Prep for midterm", "project": "{p1.name}"}}\n```'
    )
    _, reply2 = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content=f"Add a task to prep for midterm for project {p1.name}",
    )

    tasks_after = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks_after) == 2
    task2 = next(t for t in tasks_after if t.title == "Prep for midterm")
    assert task2.project_id == p1.id
    assert "Math 101" in reply2.content


@pytest.mark.asyncio
async def test_duplicate_submission_does_not_create_duplicates(
    db_session: AsyncSession, test_user: User
):
    """5. Duplicate submission with same client_message_id returns existing message without duplicate task creation."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    task_svc = TaskServiceImpl(db_session)

    conv = await service.create_conversation(owner_id=test_user.id, title="Idempotency Guard")

    # Turn 1
    u1, a1 = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Add a task to submit timesheet",
        client_message_id="idem-timesheet-001",
    )

    tasks_1 = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks_1) == 1
    first_task_id = tasks_1[0].id

    # Turn 2: Exact duplicate submission
    u2, a2 = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Add a task to submit timesheet",
        client_message_id="idem-timesheet-001",
    )

    assert u2.id == u1.id
    assert a2.id == a1.id

    # Strictly verify no second task was created in the database
    tasks_2 = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks_2) == 1
    assert tasks_2[0].id == first_task_id


@pytest.mark.asyncio
async def test_cross_user_isolation(
    db_session: AsyncSession, test_user: User, second_user: User
):
    """6. Cross-user task and conversation access is rejected."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    project_svc = ProjectServiceImpl(db_session)
    task_svc = TaskServiceImpl(db_session)

    # User A creates a private project and conversation
    proj_a = await project_svc.create_project(owner_id=test_user.id, name="Top Secret A")
    conv_a = await service.create_conversation(owner_id=test_user.id, title="User A Thread")

    # User B cannot send messages to User A's conversation
    with pytest.raises(ServiceNotFound):
        await service.send_message(
            owner_id=second_user.id,
            conversation_id=conv_a.id,
            content="Add a task from unauthorized user",
        )

    # If User B tries to associate a task with User A's project in User B's conversation
    conv_b = await service.create_conversation(owner_id=second_user.id, title="User B Thread")
    mock_hermes.custom_responses.append(
        f'```action\n{{"action": "create_task", "title": "B Task", "project": "{proj_a.name}"}}\n```'
    )
    _, reply_b = await service.send_message(
        owner_id=second_user.id,
        conversation_id=conv_b.id,
        content=f"Add a task B Task in project {proj_a.name}",
    )

    # Task was created for User B, but User A's project was NOT attached
    tasks_b = await task_svc.list_tasks(owner_id=second_user.id)
    assert len(tasks_b) == 1
    assert tasks_b[0].project_id is None
    assert "not found" in reply_b.content.lower()


@pytest.mark.asyncio
async def test_approval_creates_intended_task_once(
    db_session: AsyncSession, test_user: User
):
    """7. Approval creates the intended task once."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    task_svc = TaskServiceImpl(db_session)

    conv = await service.create_conversation(owner_id=test_user.id, title="Approval Flow")

    # Turn 1: Ambiguous intent
    _, reply1 = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="I need to finish my assignment soon",
    )
    assert len(await task_svc.list_tasks(owner_id=test_user.id)) == 0
    assert "would you like me to create a task" in reply1.content.lower()

    # Turn 2: User gives explicit approval
    _, reply2 = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Yes, create it",
    )

    # Now exactly one task has been created
    tasks = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks) == 1
    assert tasks[0].title == "Finish my assignment"
    assert str(tasks[0].id) in reply2.content


@pytest.mark.asyncio
async def test_failed_persistence_reported_honestly(
    db_session: AsyncSession, test_user: User
):
    """8. Failed persistence is reported honestly without claiming success."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()

    class FailingTaskService(TaskServiceImpl):
        async def create_task(self, *args, **kwargs):
            raise RuntimeError("PostgreSQL disk space exhausted")

    failing_task_svc = FailingTaskService(db_session)
    service = RaphaelService(
        session=db_session,
        hermes_client=mock_hermes,
        settings=settings,
        task_service=failing_task_svc,
    )

    conv = await service.create_conversation(owner_id=test_user.id, title="Failure Thread")

    mock_hermes.custom_responses.append(
        '```action\n{"action": "create_task", "title": "Impossible Task"}\n```'
    )
    _, reply = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Add a task to do the impossible",
    )

    # Verify honest reporting of failure
    assert "failed to create task" in reply.content.lower()
    assert "disk space exhausted" in reply.content.lower()
    assert "no task was created" in reply.content.lower()


@pytest.mark.asyncio
async def test_task_appears_in_existing_query_and_sync_flow(
    db_session: AsyncSession, test_user: User
):
    """9. The new task appears in the existing task query and sync flow."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    task_svc = TaskServiceImpl(db_session)

    conv = await service.create_conversation(owner_id=test_user.id, title="Sync Flow")

    _, _ = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Add a task to test sync pipeline",
    )

    tasks = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks) == 1
    task = tasks[0]
    # Verify task appears in sync_change_events for sync
    stmt = select(SyncChangeEvent).where(
        SyncChangeEvent.entity_type == "task",
        SyncChangeEvent.entity_id == task.id,
    )
    change = (await db_session.execute(stmt)).scalar_one_or_none()
    assert change is not None
    assert change.owner_id == test_user.id
    assert change.snapshot is not None
    assert "test sync pipeline" in str(change.snapshot).lower()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("OPENROUTER_API_KEY") or not shutil.which("hermes"),
    reason="Live Hermes runtime or OPENROUTER_API_KEY not configured",
)
async def test_live_hermes_real_model_smoke_test(
    db_session: AsyncSession, test_user: User
):
    """10. Real-model integration smoke test using host Hermes runtime when credentials exist."""
    settings = Settings(
        environment="test",
        hermes_binary_path=shutil.which("hermes") or "/home/peskybird/.local/bin/hermes",
        hermes_skill_name="personal-os",
    )
    live_hermes = LiveHermesClient(
        binary_path=settings.hermes_binary_path,
        default_skill=settings.hermes_skill_name,
    )
    service = RaphaelService(session=db_session, hermes_client=live_hermes, settings=settings)
    task_svc = TaskServiceImpl(db_session)

    conv = await service.create_conversation(owner_id=test_user.id, title="Live Model Smoke Test")

    _, assistant_msg = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Add a task to inspect cloud deployment logs tomorrow",
    )

    tasks = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks) == 1, f"Expected 1 task but got 0. Reply: {assistant_msg.content}"
    assert "inspect" in tasks[0].title.lower()
    assert str(tasks[0].id) in assistant_msg.content


@pytest.mark.asyncio
async def test_implicit_obligation_category_b_asks_clarification(
    db_session: AsyncSession, test_user: User
):
    """Category B: User mentions obligation with soft deadline; Raphael asks concise 3-way clarification."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    task_svc = TaskServiceImpl(db_session)

    conv = await service.create_conversation(owner_id=test_user.id, title="WWTP BOM Obligation")

    _, assistant_msg = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="I have to submit the BOM for the WWTP to Somya Sir by tomorrow. It's a soft deadline.",
    )

    # In Category B, no task is created yet
    tasks = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks) == 0

    # Raphael asks the 3-choice clarification question
    assert "should i add" in assistant_msg.content.lower()
    assert "blockday" in assistant_msg.content.lower()
    assert "both" in assistant_msg.content.lower()


@pytest.mark.asyncio
async def test_category_b_turn2_add_to_blockday_creates_task_with_soft_deadline_and_context(
    db_session: AsyncSession, test_user: User
):
    """Category B: User selects 'Add to Blockday'; task is created with soft deadline qualification and context."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    task_svc = TaskServiceImpl(db_session)

    conv = await service.create_conversation(owner_id=test_user.id, title="WWTP BOM Task Tracking")

    # Turn 1: Obligation
    await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="I have to submit the BOM for the WWTP to Somya Sir by tomorrow. It's a soft deadline.",
    )

    # Turn 2: User requests tracking
    _, assistant_msg2 = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Add to Blockday",
    )

    # Task is created
    tasks = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks) == 1
    task = tasks[0]
    assert "BOM" in task.title
    assert task.due_date is not None
    assert task.description is not None
    assert "soft deadline" in task.description.lower()
    assert "Somya Sir" in task.description

    assert "I have created the task" in assistant_msg2.content
    assert str(task.id) in assistant_msg2.content
    assert "Description:" in assistant_msg2.content


@pytest.mark.asyncio
async def test_category_b_turn2_work_on_it_now_assists_without_creating_task(
    db_session: AsyncSession, test_user: User
):
    """Category B: User selects 'Work on it now'; Raphael assists directly without creating a task."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    task_svc = TaskServiceImpl(db_session)

    conv = await service.create_conversation(owner_id=test_user.id, title="WWTP BOM Preparation")

    # Turn 1: Obligation
    await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="I have to submit the BOM for the WWTP to Somya Sir by tomorrow. It's a soft deadline.",
    )

    # Turn 2: Work on it now
    _, assistant_msg2 = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Help me prepare it now",
    )

    # No task created
    tasks = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks) == 0

    # Assistant gives direct assistance
    assert "let's prepare" in assistant_msg2.content.lower() or "bom" in assistant_msg2.content.lower()
    assert "I have created the task" not in assistant_msg2.content


@pytest.mark.asyncio
async def test_category_b_turn2_both_creates_task_and_assists(
    db_session: AsyncSession, test_user: User
):
    """Category B: User selects 'Both'; Raphael creates the task and provides immediate assistance."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    task_svc = TaskServiceImpl(db_session)

    conv = await service.create_conversation(owner_id=test_user.id, title="WWTP BOM Both")

    # Turn 1: Obligation
    await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="I have to submit the BOM for the WWTP to Somya Sir by tomorrow. It's a soft deadline.",
    )

    # Turn 2: Both
    _, assistant_msg2 = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Both",
    )

    # Task is created
    tasks = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks) == 1
    assert "I have created the task" in assistant_msg2.content
    assert "here is an outline" in assistant_msg2.content.lower() or "bom" in assistant_msg2.content.lower()


@pytest.mark.asyncio
async def test_explicit_task_creation_category_a_creates_task_directly(
    db_session: AsyncSession, test_user: User
):
    """Category A: Explicit command to create task bypasses clarification."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    task_svc = TaskServiceImpl(db_session)

    conv = await service.create_conversation(owner_id=test_user.id, title="Explicit Task")

    _, assistant_msg = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Add a task: Review PR 42 before Friday",
    )

    tasks = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks) == 1
    assert "Review PR 42" in tasks[0].title
    assert "I have created the task" in assistant_msg.content


@pytest.mark.asyncio
async def test_casual_conversation_category_e_does_not_create_task(
    db_session: AsyncSession, test_user: User
):
    """Category E: Casual statement responds naturally without forcing task creation."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    task_svc = TaskServiceImpl(db_session)

    conv = await service.create_conversation(owner_id=test_user.id, title="Casual Chat")

    _, assistant_msg = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Good morning, hope you're having a productive day!",
    )

    tasks = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks) == 0
    assert "I have created the task" not in assistant_msg.content


@pytest.mark.asyncio
async def test_diary_session_summary_distinguishes_created_tasks_from_work_discussed(
    db_session: AsyncSession, test_user: User
):
    """Diary summarization records unscheduled work discussed separately from confirmed created tasks."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)

    conv = await service.create_conversation(owner_id=test_user.id, title="Unscheduled Work Discussion")

    # Turn 1: User mentions work obligation, but chooses to only work on it now (no task created)
    await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="I have to submit the BOM for the WWTP to Somya Sir by tomorrow.",
    )
    await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Help me prepare it now",
    )

    # Force session finalization into Diary
    capture = await service.finalize_conversation(conv.id, force=True)
    assert capture is not None
    assert capture.metadata_json is not None

    # Confirmed tasks list is empty because no task was created
    assert len(capture.metadata_json["tasks"]) == 0
    # Work discussed is recorded in metadata
    assert len(capture.metadata_json["work_discussed"]) >= 1
    assert any("BOM" in w or "WWTP" in w for w in capture.metadata_json["work_discussed"])

    # Markdown output has Work Discussed (Unscheduled) section, not Created Tasks section
    assert "### Work Discussed (Unscheduled)" in capture.content
    assert "### Created Tasks" not in capture.content
