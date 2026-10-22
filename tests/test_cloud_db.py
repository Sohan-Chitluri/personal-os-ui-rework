"""Unit and mock regression tests for cloud & database correctness findings:

- Finding #5: Expired sync-feed cursors prune in get_change_page
- Finding #10: Delete operations optimistic concurrency control (If-Match -> 412)
- Finding #12: Paginated task/project APIs report true total count
- Finding #16: Capture processing eliminates redundant get_by_capture DB query
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from sqlalchemy.sql import Delete

from personal_os.api.routes.projects import delete_project, list_projects
from personal_os.api.routes.tasks import delete_task, list_tasks
from personal_os.api.schemas import ProjectListResponse, TaskListResponse
from personal_os.db.models import Capture, CaptureProcessing, Project, Task, User
from personal_os.db.repositories import StaleRevisionError
from personal_os.db.repositories.sqlalchemy import (
    SqlCaptureRepository,
    SqlProjectRepository,
    SqlTaskRepository,
)
from personal_os.services.interfaces import ServiceConflictError, ServiceNotFound
from personal_os.services.processing import ProcessingServiceImpl
from personal_os.services.tasks import ProjectServiceImpl, TaskServiceImpl
from personal_os.sync.contracts import EntityType, OperationType, SyncOperation
from personal_os.sync.postgres_store import (
    ChangeFeedPage,
    ExpiredSyncCursorError,
    InvalidSyncCursorError,
    PostgresSyncStore,
    SyncFeedCursor,
)


# ==============================================================================
# Finding #5: Expired sync-feed cursors prune in get_change_page
# ==============================================================================


@pytest.mark.asyncio
async def test_finding_5_get_change_page_prunes_expired_cursors():
    """get_change_page must execute a DELETE statement for expired cursors belonging to owner."""
    owner_id = "01JTESTOWNER00000000000000"

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.begin = MagicMock()
    mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=None)

    # Session.execute mock for cursor delete + change event select
    executed_statements = []

    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_result.scalars.return_value = mock_scalars

    async def fake_execute(statement, *args, **kwargs):
        executed_statements.append(statement)
        return mock_result

    mock_session.execute = AsyncMock(side_effect=fake_execute)

    mock_session_factory = MagicMock()
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session_factory.kw = {"bind": mock_bind}
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=None)

    store = PostgresSyncStore(mock_session_factory)
    page = await store.get_change_page(owner_id)

    assert isinstance(page, ChangeFeedPage)
    assert page.items == ()

    # Verify that a delete statement was executed for SyncFeedCursor
    delete_stmts = [s for s in executed_statements if isinstance(s, Delete)]
    assert len(delete_stmts) == 1
    delete_stmt = delete_stmts[0]
    assert delete_stmt.table.name == "sync_feed_cursors"

    # Verify a cursor was added and flushed
    assert mock_session.add.called
    added_cursor = mock_session.add.call_args[0][0]
    assert isinstance(added_cursor, SyncFeedCursor)
    assert added_cursor.owner_id == owner_id
    assert mock_session.flush.called


@pytest.mark.asyncio
async def test_finding_5_expired_cursor_raises_expired_sync_cursor_error():
    """get_change_page raises ExpiredSyncCursorError when supplied cursor has expired."""
    owner_id = "01JTESTOWNER00000000000000"

    mock_session = AsyncMock()
    mock_session.begin = MagicMock()
    mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=None)

    # Return an expired cursor from session.get
    now = datetime.now(UTC)
    expired_cursor_record = MagicMock()
    expired_cursor_record.owner_id = owner_id
    expired_cursor_record.expires_at = now - timedelta(hours=1)
    mock_session.get = AsyncMock(return_value=expired_cursor_record)

    mock_session_factory = MagicMock()
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session_factory.kw = {"bind": mock_bind}
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=None)

    store = PostgresSyncStore(mock_session_factory)
    with pytest.raises(ExpiredSyncCursorError):
        # 32 characters minimum for cursor length
        await store.get_change_page(owner_id, cursor="a" * 32)


# ==============================================================================
# Finding #10: Delete operations lack optimistic concurrency control
# ==============================================================================


@pytest.mark.asyncio
async def test_finding_10_delete_task_route_with_matching_revision():
    """DELETE /api/v1/tasks/{task_id} succeeds with 204 when If-Match matches."""
    user = User(id="01JUSER0000000000000000000", email="test@example.com", cognito_sub="sub-1")
    service = AsyncMock()
    service.delete_task = AsyncMock(return_value=None)

    await delete_task(
        task_id="task-123",
        current_user=user,
        service=service,
        expected_revision=1,
    )

    service.delete_task.assert_awaited_once_with(
        owner_id=user.id,
        task_id="task-123",
        expected_revision=1,
    )


@pytest.mark.asyncio
async def test_finding_10_delete_task_route_raises_412_on_revision_mismatch():
    """DELETE /api/v1/tasks/{task_id} raises HTTP 412 Precondition Failed when revision doesn't match."""
    user = User(id="01JUSER0000000000000000000", email="test@example.com", cognito_sub="sub-1")
    service = AsyncMock()
    service.delete_task = AsyncMock(side_effect=ServiceConflictError("task", "task-123", 2))

    with pytest.raises(HTTPException) as exc_info:
        await delete_task(
            task_id="task-123",
            current_user=user,
            service=service,
            expected_revision=1,
        )

    assert exc_info.value.status_code == status.HTTP_412_PRECONDITION_FAILED
    assert "revision mismatch" in exc_info.value.detail


@pytest.mark.asyncio
async def test_finding_10_delete_project_route_raises_412_on_revision_mismatch():
    """DELETE /api/v1/projects/{project_id} raises HTTP 412 Precondition Failed when revision doesn't match."""
    user = User(id="01JUSER0000000000000000000", email="test@example.com", cognito_sub="sub-1")
    service = AsyncMock()
    service.delete_project = AsyncMock(side_effect=ServiceConflictError("project", "proj-123", 3))

    with pytest.raises(HTTPException) as exc_info:
        await delete_project(
            project_id="proj-123",
            current_user=user,
            service=service,
            expected_revision=1,
        )

    assert exc_info.value.status_code == status.HTTP_412_PRECONDITION_FAILED
    assert "revision mismatch" in exc_info.value.detail


@pytest.mark.asyncio
async def test_finding_10_task_service_delete_enforces_revision_comparison():
    """TaskServiceImpl.delete_task passes expected_revision to repository and handles StaleRevisionError."""
    session = AsyncMock()
    service = TaskServiceImpl(session)
    service.users.get = AsyncMock(return_value=MagicMock())  # user exists

    # Case A: soft_delete raises StaleRevisionError and task exists -> raises ServiceConflictError
    service.tasks.soft_delete = AsyncMock(side_effect=StaleRevisionError("Task", "task-1", 3))
    existing_task = MagicMock(id="task-1", revision=3)
    service.tasks.get = AsyncMock(return_value=existing_task)

    with pytest.raises(ServiceConflictError) as exc_info:
        await service.delete_task("owner-1", "task-1", expected_revision=2)

    assert exc_info.value.entity == "task"
    assert exc_info.value.current_revision == 3
    service.tasks.soft_delete.assert_awaited_with("task-1", "owner-1", expected_revision=2)

    # Case B: soft_delete returns False and task does not exist -> raises ServiceNotFound
    service.tasks.soft_delete = AsyncMock(return_value=False)
    service.tasks.get = AsyncMock(return_value=None)

    with pytest.raises(ServiceNotFound):
        await service.delete_task("owner-1", "task-1", expected_revision=2)


@pytest.mark.asyncio
async def test_finding_10_project_service_delete_enforces_revision_comparison():
    """ProjectServiceImpl.delete_project passes expected_revision to repository and handles StaleRevisionError."""
    session = AsyncMock()
    service = ProjectServiceImpl(session)
    service.users.get = AsyncMock(return_value=MagicMock())

    service.projects.soft_delete = AsyncMock(side_effect=StaleRevisionError("Project", "proj-1", 4))
    existing_project = MagicMock(id="proj-1", revision=4)
    service.projects.get = AsyncMock(return_value=existing_project)

    with pytest.raises(ServiceConflictError) as exc_info:
        await service.delete_project("owner-1", "proj-1", expected_revision=1)

    assert exc_info.value.entity == "project"
    assert exc_info.value.current_revision == 4
    service.projects.soft_delete.assert_awaited_with("proj-1", "owner-1", expected_revision=1)


@pytest.mark.asyncio
async def test_finding_10_sql_repositories_soft_delete_invokes_guarded_update():
    """SqlTaskRepository and SqlProjectRepository soft_delete delegate to _guarded_update with expected_revision."""
    session = AsyncMock()
    task_repo = SqlTaskRepository(session)
    task_repo._guarded_update = AsyncMock(return_value=MagicMock())

    result = await task_repo.soft_delete("t1", "o1", expected_revision=5)
    assert result is True
    task_repo._guarded_update.assert_awaited_once()
    args, kwargs = task_repo._guarded_update.call_args
    assert args[0] == Task
    assert args[1] == "t1"
    assert args[2] == "o1"
    assert args[3] == 5  # expected_revision
    assert args[4]["status"] == "deleted"
    assert "deleted_at" in args[4]

    proj_repo = SqlProjectRepository(session)
    proj_repo._guarded_update = AsyncMock(return_value=MagicMock())
    result_p = await proj_repo.soft_delete("p1", "o1", expected_revision=2)
    assert result_p is True
    p_args, _ = proj_repo._guarded_update.call_args
    assert p_args[0] == Project
    assert p_args[1] == "p1"
    assert p_args[2] == "o1"
    assert p_args[3] == 2
    assert p_args[4]["status"] == "deleted"


# ==============================================================================
# Finding #12: Paginated task/project APIs report page size as total count
# ==============================================================================


@pytest.mark.asyncio
async def test_finding_12_list_tasks_reports_true_total_count():
    """GET /api/v1/tasks returns true total count from service.count_tasks, not len(items)."""
    user = User(id="01JUSER0000000000000000000", email="test@example.com", cognito_sub="sub-1")

    # Mock 2 items on the current page, but total matching tasks is 42
    now = datetime.now(UTC)
    task1 = Task(id="t1", owner_id=user.id, title="Task 1", status="open", revision=1, created_at=now, updated_at=now)
    task2 = Task(id="t2", owner_id=user.id, title="Task 2", status="open", revision=1, created_at=now, updated_at=now)

    service = AsyncMock()
    service.list_tasks = AsyncMock(return_value=[task1, task2])
    service.count_tasks = AsyncMock(return_value=42)

    response = await list_tasks(
        current_user=user,
        service=service,
        limit=2,
        offset=0,
    )

    assert isinstance(response, TaskListResponse)
    assert len(response.items) == 2
    assert response.total == 42  # True count, not len(items) == 2
    service.count_tasks.assert_awaited_once_with(
        owner_id=user.id,
        project_id=None,
        status=None,
    )


@pytest.mark.asyncio
async def test_finding_12_list_projects_reports_true_total_count():
    """GET /api/v1/projects returns true total count from service.count_projects, not len(items)."""
    user = User(id="01JUSER0000000000000000000", email="test@example.com", cognito_sub="sub-1")

    now = datetime.now(UTC)
    proj1 = Project(id="p1", owner_id=user.id, name="Project 1", status="active", revision=1, created_at=now, updated_at=now)

    service = AsyncMock()
    service.list_projects = AsyncMock(return_value=[proj1])
    service.count_projects = AsyncMock(return_value=15)

    response = await list_projects(
        current_user=user,
        service=service,
        limit=1,
        offset=0,
    )

    assert isinstance(response, ProjectListResponse)
    assert len(response.items) == 1
    assert response.total == 15  # True count, not len(items) == 1
    service.count_projects.assert_awaited_once_with(
        owner_id=user.id,
        status=None,
    )


@pytest.mark.asyncio
async def test_finding_12_service_count_methods_delegate_to_repository():
    """TaskServiceImpl.count_tasks and ProjectServiceImpl.count_projects call repository count_for_owner."""
    session = AsyncMock()
    task_svc = TaskServiceImpl(session)
    task_svc.users.get = AsyncMock(return_value=MagicMock())
    task_svc.tasks.count_for_owner = AsyncMock(return_value=99)

    count_t = await task_svc.count_tasks("owner-1", project_id="p-1", status="open")
    assert count_t == 99
    task_svc.tasks.count_for_owner.assert_awaited_once_with(
        "owner-1", project_id="p-1", status="open"
    )

    proj_svc = ProjectServiceImpl(session)
    proj_svc.users.get = AsyncMock(return_value=MagicMock())
    proj_svc.projects.count_for_owner = AsyncMock(return_value=27)

    count_p = await proj_svc.count_projects("owner-1", status="active")
    assert count_p == 27
    proj_svc.projects.count_for_owner.assert_awaited_once_with("owner-1", status="active")


# ==============================================================================
# Finding #16: Capture processing performs a redundant database query
# ==============================================================================


@pytest.mark.asyncio
async def test_finding_16_processing_service_reuses_job_id_directly():
    """ProcessingServiceImpl._run_pipeline reuses job.id directly without calling get_by_capture."""
    session = AsyncMock()
    job = CaptureProcessing(
        id="job-uuid-12345",
        capture_id="cap-uuid-67890",
        status="processing",
        attempt=1,
        max_attempts=3,
    )
    capture = Capture(
        id="cap-uuid-67890",
        owner_id="owner-1",
        content="Test capture note",
        source="web",
    )

    # Mock extractor
    mock_extractor = AsyncMock()
    mock_extractor_result = MagicMock()
    mock_extractor_result.items = []
    mock_extractor_result.errors = []
    mock_extractor_result.source_text = capture.content
    mock_extractor.extract = AsyncMock(return_value=mock_extractor_result)

    service = ProcessingServiceImpl(session, extractor=mock_extractor)

    # Mock existing task query and context builder
    service._existing_task_titles = AsyncMock(return_value={})
    service._build_context = AsyncMock(return_value=MagicMock())

    # Mock processing repository
    service.processing.update = AsyncMock(return_value=job)
    service.processing.get_by_capture = AsyncMock()

    # Call _run_pipeline passing the already-known job
    task_ids = await service._run_pipeline(capture, "owner-1", job=job)
    assert task_ids == []

    # Verify self.processing.update was called directly with job.id
    service.processing.update.assert_awaited_once()
    called_job_id = service.processing.update.call_args[0][0]
    assert called_job_id == "job-uuid-12345"

    # Crucial assertion: get_by_capture was NEVER called!
    service.processing.get_by_capture.assert_not_called()


@pytest.mark.asyncio
async def test_finding_10_delete_task_route_without_if_match_succeeds():
    """DELETE /api/v1/tasks/{task_id} succeeds with 204 when expected_revision is None."""
    user = User(id="01JUSER0000000000000000000", email="test@example.com", cognito_sub="sub-1")
    service = AsyncMock()
    service.delete_task = AsyncMock(return_value=None)

    await delete_task(
        task_id="task-123",
        current_user=user,
        service=service,
        expected_revision=None,
    )

    service.delete_task.assert_awaited_once_with(
        owner_id=user.id,
        task_id="task-123",
        expected_revision=None,
    )


@pytest.mark.asyncio
async def test_finding_10_delete_project_route_without_if_match_succeeds():
    """DELETE /api/v1/projects/{project_id} succeeds with 204 when expected_revision is None."""
    user = User(id="01JUSER0000000000000000000", email="test@example.com", cognito_sub="sub-1")
    service = AsyncMock()
    service.delete_project = AsyncMock(return_value=None)

    await delete_project(
        project_id="proj-123",
        current_user=user,
        service=service,
        expected_revision=None,
    )

    service.delete_project.assert_awaited_once_with(
        owner_id=user.id,
        project_id="proj-123",
        expected_revision=None,
    )


@pytest.mark.asyncio
async def test_finding_12_repository_count_queries_apply_filters():
    """SqlTaskRepository.count_for_owner and SqlProjectRepository.count_for_owner execute correct count queries."""
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=7)

    task_repo = SqlTaskRepository(session)
    count = await task_repo.count_for_owner("o1", project_id="p1", status="open")
    assert count == 7
    session.scalar.assert_awaited_once()

    session.scalar.reset_mock()
    session.scalar = AsyncMock(return_value=3)

    proj_repo = SqlProjectRepository(session)
    p_count = await proj_repo.count_for_owner("o1", status="active")
    assert p_count == 3
    session.scalar.assert_awaited_once()

