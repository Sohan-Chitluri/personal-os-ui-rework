"""Tests for Raphael natural-language project creation, validation, duplicate handling, and authorization."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.config import Settings
from personal_os.db.models import User
from personal_os.services.hermes import MockHermesClient
from personal_os.services.interfaces import ServiceNotFound
from personal_os.services.raphael import RaphaelService
from personal_os.services.tasks import ProjectServiceImpl, TaskServiceImpl


@pytest_asyncio.fixture
async def second_user(db_session: AsyncSession) -> User:
    user = User(
        cognito_sub="second-user-sub-proj",
        email="second-user-proj@example.com",
        display_name="Second Project User",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_explicit_project_creation_without_description(
    db_session: AsyncSession, test_user: User
):
    """1. Explicit project creation without description creates project and returns clean confirmation without DB IDs."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    project_svc = ProjectServiceImpl(db_session)

    conv = await service.create_conversation(owner_id=test_user.id, title="Project Creation")

    # Send explicit project request
    user_msg, assistant_msg = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Add a project called CORDIC Accelerator.",
        client_message_id="proj-turn-1",
    )

    assert user_msg.content == "Add a project called CORDIC Accelerator."

    # Verify project persisted through project service
    projects = await project_svc.list_projects(owner_id=test_user.id)
    assert len(projects) == 1
    proj = projects[0]
    assert proj.name == "CORDIC Accelerator"
    assert proj.description is None
    assert proj.status == "active"
    assert proj.owner_id == test_user.id

    # Verify natural-language confirmation without database IDs or raw internal details
    assert "I have created the project **CORDIC Accelerator**." in assistant_msg.content
    assert str(proj.id) not in assistant_msg.content
    assert "ID:" not in assistant_msg.content
    assert "revision" not in assistant_msg.content.lower()


@pytest.mark.asyncio
async def test_explicit_project_creation_with_description(
    db_session: AsyncSession, test_user: User
):
    """2. Explicit project creation with description parses context and persists description."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    project_svc = ProjectServiceImpl(db_session)

    conv = await service.create_conversation(owner_id=test_user.id, title="Course Project")

    # Send request with contextual description
    _, assistant_msg = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Create a project for my ASIC Design course called CORDIC Accelerator.",
        client_message_id="proj-turn-desc",
    )

    projects = await project_svc.list_projects(owner_id=test_user.id)
    assert len(projects) == 1
    proj = projects[0]
    assert proj.name == "CORDIC Accelerator"
    assert proj.description is not None
    assert "ASIC Design" in proj.description

    assert "I have created the project **CORDIC Accelerator**." in assistant_msg.content
    assert "- Description:" in assistant_msg.content
    assert str(proj.id) not in assistant_msg.content
    assert "ID:" not in assistant_msg.content


@pytest.mark.asyncio
async def test_validation_empty_project_name(
    db_session: AsyncSession, test_user: User
):
    """3. Validation rejects empty or blank project names without persisting."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    project_svc = ProjectServiceImpl(db_session)

    conv = await service.create_conversation(owner_id=test_user.id, title="Empty Project Name")

    mock_hermes.custom_responses.append(
        '```action\n{"action": "create_project", "name": "   "}\n```'
    )
    _, assistant_msg = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Create project with no name",
    )

    projects = await project_svc.list_projects(owner_id=test_user.id)
    assert len(projects) == 0
    assert "project name was empty" in assistant_msg.content.lower()


@pytest.mark.asyncio
async def test_duplicate_project_handling(
    db_session: AsyncSession, test_user: User
):
    """4. Duplicate or existing project names are safely rejected without creating duplicates."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    project_svc = ProjectServiceImpl(db_session)

    # Pre-create a project
    await project_svc.create_project(owner_id=test_user.id, name="CORDIC Accelerator")

    conv = await service.create_conversation(owner_id=test_user.id, title="Duplicate Check")

    # Attempt to create duplicate (case-insensitive test: "cordic accelerator")
    mock_hermes.custom_responses.append(
        '```action\n{"action": "create_project", "name": "cordic accelerator"}\n```'
    )
    _, assistant_msg = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Add a project called cordic accelerator",
    )

    # Confirm exactly 1 project remains
    projects = await project_svc.list_projects(owner_id=test_user.id)
    assert len(projects) == 1
    assert "already exists" in assistant_msg.content.lower()
    assert "no new project was created" in assistant_msg.content.lower()


@pytest.mark.asyncio
async def test_cross_user_project_isolation(
    db_session: AsyncSession, test_user: User, second_user: User
):
    """5. Project creation enforces owner-scoping: users can have same-named projects independently."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    project_svc = ProjectServiceImpl(db_session)

    # User A creates "Shared Name"
    conv_a = await service.create_conversation(owner_id=test_user.id, title="User A Thread")
    await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv_a.id,
        content="Add a project called Shared Name",
    )

    # User B creates "Shared Name" in their own conversation
    conv_b = await service.create_conversation(owner_id=second_user.id, title="User B Thread")
    _, reply_b = await service.send_message(
        owner_id=second_user.id,
        conversation_id=conv_b.id,
        content="Add a project called Shared Name",
    )

    assert "I have created the project **Shared Name**." in reply_b.content

    # Both users have their own project
    proj_a_list = await project_svc.list_projects(owner_id=test_user.id)
    proj_b_list = await project_svc.list_projects(owner_id=second_user.id)
    assert len(proj_a_list) == 1
    assert len(proj_b_list) == 1
    assert proj_a_list[0].id != proj_b_list[0].id
    assert proj_a_list[0].owner_id == test_user.id
    assert proj_b_list[0].owner_id == second_user.id

    # User B cannot message User A's conversation
    with pytest.raises(ServiceNotFound):
        await service.send_message(
            owner_id=second_user.id,
            conversation_id=conv_a.id,
            content="Attempt unauthorized project addition",
        )


@pytest.mark.asyncio
async def test_subsequent_task_associates_with_new_project(
    db_session: AsyncSession, test_user: User
):
    """6. Tasks created after project creation can associate with the newly created project."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    task_svc = TaskServiceImpl(db_session)
    project_svc = ProjectServiceImpl(db_session)

    conv = await service.create_conversation(owner_id=test_user.id, title="Workflow Flow")

    # Turn 1: Create project
    await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Add a project called CORDIC Accelerator.",
    )

    projects = await project_svc.list_projects(owner_id=test_user.id)
    assert len(projects) == 1
    proj = projects[0]

    # Turn 2: Create task targeting that project
    _, reply_task = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Add a task to implement sine LUT in project CORDIC Accelerator tomorrow",
    )

    tasks = await task_svc.list_tasks(owner_id=test_user.id)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.title == "Implement sine LUT"
    assert task.project_id == proj.id
    assert task.due_date is not None
    assert "CORDIC Accelerator" in reply_task.content


@pytest.mark.asyncio
async def test_failed_project_persistence_reported_honestly(
    db_session: AsyncSession, test_user: User
):
    """7. Failed project persistence is reported honestly without claiming success."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()

    class FailingProjectService(ProjectServiceImpl):
        async def create_project(self, *args, **kwargs):
            raise RuntimeError("Database connection timed out")

    failing_proj_svc = FailingProjectService(db_session)
    service = RaphaelService(
        session=db_session,
        hermes_client=mock_hermes,
        settings=settings,
        project_service=failing_proj_svc,
    )

    conv = await service.create_conversation(owner_id=test_user.id, title="Failure Project Thread")

    mock_hermes.custom_responses.append(
        '```action\n{"action": "create_project", "name": "Doomed Project"}\n```'
    )
    _, reply = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="Add a project called Doomed Project",
    )

    assert "failed to create project" in reply.content.lower()
    assert "timed out" in reply.content.lower()
    assert "no project was created" in reply.content.lower()


@pytest.mark.asyncio
async def test_ambiguous_project_intent_asks_clarification(
    db_session: AsyncSession, test_user: User
):
    """8. Ambiguous project intent asks clarification without creating a project."""
    settings = Settings(environment="test")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    project_svc = ProjectServiceImpl(db_session)

    conv = await service.create_conversation(owner_id=test_user.id, title="Ambiguous Project")

    _, assistant_msg = await service.send_message(
        owner_id=test_user.id,
        conversation_id=conv.id,
        content="I want to start a new project soon",
    )

    # Assert no project created
    projects = await project_svc.list_projects(owner_id=test_user.id)
    assert len(projects) == 0

    assert "would you like me to create a project" in assistant_msg.content.lower()


@pytest.mark.asyncio
async def test_connect_repository_action(db_session: AsyncSession, test_user: User):
    """9. Raphael executes connect_repository action and associates repo to existing project."""
    from unittest.mock import AsyncMock, patch
    from personal_os.services.github import GitHubClient

    settings = Settings(environment="test", github_token_encryption_key="test-key")
    mock_hermes = MockHermesClient()
    service = RaphaelService(session=db_session, hermes_client=mock_hermes, settings=settings)
    project_svc = ProjectServiceImpl(db_session)

    # Create project first
    project = await project_svc.create_project(owner_id=test_user.id, name="CORDIC Core")

    # Connect user's GitHub
    with patch.object(
        GitHubClient,
        "get_authenticated_user",
        new=AsyncMock(return_value={"id": "1", "login": "alice"}),
    ):
        await service.github_service.connect_token(test_user.id, "ghp_tok")

    conv = await service.create_conversation(owner_id=test_user.id, title="Repo Connect")

    mock_hermes.custom_responses.append(
        '```action\n{"action": "connect_repository", "project": "CORDIC Core", "repository": "alice/cordic-core"}\n```'
    )

    mock_repo = {
        "id": "101",
        "name": "cordic-core",
        "full_name": "alice/cordic-core",
        "owner": "alice",
        "html_url": "https://github.com/alice/cordic-core",
        "default_branch": "main",
    }
    with (
        patch.object(GitHubClient, "get_repository", new=AsyncMock(return_value=mock_repo)),
        patch.object(GitHubClient, "get_readme", new=AsyncMock(return_value=("# CORDIC\nPipelined math.", "sha1"))),
    ):
        _, reply = await service.send_message(
            owner_id=test_user.id,
            conversation_id=conv.id,
            content="Yes, connect alice/cordic-core to CORDIC Core",
        )

        assert "Connected repository **alice/cordic-core** to project **CORDIC Core**" in reply.content
        assoc = await service.github_service.get_project_repository(test_user.id, project.id)
        assert assoc is not None
        assert assoc.full_name == "alice/cordic-core"
