"""Unit and integration tests for typed application tools and PersonalOSToolExecutor."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from personal_os.db.models import Project, Task
from personal_os.services.assistant_tools import (
    PersonalOSToolExecutor,
    get_personal_os_tools,
    resolve_date_expression,
)
from personal_os.services.interfaces import (
    ProjectService,
    ServiceNotFound,
    TaskService,
)


@pytest.fixture
def fixed_now() -> datetime:
    # A fixed Tuesday at 14:30:00 UTC
    return datetime(2026, 10, 20, 14, 30, 0, tzinfo=UTC)


def test_tool_definitions_registry() -> None:
    tools = get_personal_os_tools()
    assert len(tools) == 6
    names = [t.name for t in tools]
    assert "search_tasks" in names
    assert "get_task" in names
    assert "list_projects" in names
    assert "get_project" in names
    assert "resolve_relative_date" in names
    assert "propose_task_action" in names


def test_resolve_date_expression_keywords(fixed_now: datetime) -> None:
    # Today -> 2026-10-20 23:59:59
    today_dt, err = resolve_date_expression("today", fixed_now)
    assert err is None
    assert today_dt is not None
    assert today_dt.year == 2026 and today_dt.month == 10 and today_dt.day == 20
    assert today_dt.hour == 23 and today_dt.minute == 59

    # Tomorrow -> 2026-10-21 23:59:59
    tom_dt, err = resolve_date_expression("tomorrow", fixed_now)
    assert err is None
    assert tom_dt is not None
    assert tom_dt.day == 21

    # in 3 days -> 2026-10-23
    in3_dt, err = resolve_date_expression("in 3 days", fixed_now)
    assert err is None
    assert in3_dt is not None
    assert in3_dt.day == 23


def test_resolve_date_expression_weekdays(fixed_now: datetime) -> None:
    # 2026-10-20 is Tuesday (weekday 1)
    # "this Friday" or "Friday" -> +3 days -> 2026-10-23
    fri_dt, err = resolve_date_expression("this friday", fixed_now)
    assert err is None
    assert fri_dt is not None
    assert fri_dt.day == 23

    # "next Monday" -> +6 days -> 2026-10-26
    mon_dt, err = resolve_date_expression("next monday", fixed_now)
    assert err is None
    assert mon_dt is not None
    assert mon_dt.day == 26


def test_resolve_date_expression_invalid(fixed_now: datetime) -> None:
    dt, err = resolve_date_expression("someday maybe", fixed_now)
    assert dt is None
    assert err is not None
    assert "Unrecognized date format" in err


@pytest.fixture
def mock_task_service() -> MagicMock:
    service = MagicMock(spec=TaskService)
    service.create_task = AsyncMock()
    service.get_task = AsyncMock()
    service.list_tasks = AsyncMock()
    service.update_task = AsyncMock()
    service.complete_task = AsyncMock()
    service.delete_task = AsyncMock()
    return service


@pytest.fixture
def mock_project_service() -> MagicMock:
    service = MagicMock(spec=ProjectService)
    service.create_project = AsyncMock()
    service.get_project = AsyncMock()
    service.list_projects = AsyncMock()
    service.update_project = AsyncMock()
    service.delete_project = AsyncMock()
    return service


@pytest.fixture
def executor(
    mock_task_service: MagicMock, mock_project_service: MagicMock, fixed_now: datetime
) -> PersonalOSToolExecutor:
    return PersonalOSToolExecutor(
        owner_id="user_123",
        task_service=mock_task_service,
        project_service=mock_project_service,
        ref_time=fixed_now,
    )


@pytest.mark.asyncio
async def test_search_tasks_query_filter(
    executor: PersonalOSToolExecutor, mock_task_service: MagicMock
) -> None:
    t1 = Task(id="t1", owner_id="user_123", title="Fix CORDIC bug", status="todo", revision=1)
    t2 = Task(id="t2", owner_id="user_123", title="Write weekly review", status="todo", revision=2)
    mock_task_service.list_tasks.return_value = [t1, t2]

    # Query matches only CORDIC
    res = await executor.execute_tool("search_tasks", {"query": "cordic"})
    mock_task_service.list_tasks.assert_awaited_once_with("user_123", limit=20)
    assert res["count"] == 1
    assert res["tasks"][0]["id"] == "t1"


@pytest.mark.asyncio
async def test_get_task_not_found(
    executor: PersonalOSToolExecutor, mock_task_service: MagicMock
) -> None:
    mock_task_service.get_task.side_effect = ServiceNotFound("task", "t999")
    res = await executor.execute_tool("get_task", {"task_id": "t999"})
    assert "error" in res
    assert "not found" in res["error"]


@pytest.mark.asyncio
async def test_get_project_fuzzy_and_ambiguous(
    executor: PersonalOSToolExecutor, mock_project_service: MagicMock
) -> None:
    p1 = Project(id="p1", owner_id="user_123", name="VLSI Design", status="active", revision=1)
    p2 = Project(
        id="p2", owner_id="user_123", name="VLSI Verification", status="active", revision=1
    )
    mock_project_service.list_projects.return_value = [p1, p2]

    # Ambiguous match for "VLSI"
    res = await executor.execute_tool("get_project", {"name": "VLSI"})
    assert "Ambiguous project name" in res.get("error", "")
    assert len(res.get("candidates", [])) == 2

    # Exact match for "VLSI Design"
    res_exact = await executor.execute_tool("get_project", {"name": "VLSI Design"})
    assert res_exact["id"] == "p1"
    assert res_exact["name"] == "VLSI Design"


@pytest.mark.asyncio
async def test_propose_task_action_create_valid(
    executor: PersonalOSToolExecutor, mock_project_service: MagicMock
) -> None:
    p1 = Project(id="p1", owner_id="user_123", name="Personal OS", status="active", revision=1)
    mock_project_service.get_project.side_effect = ServiceNotFound("project", "Personal OS")
    mock_project_service.list_projects.return_value = [p1]

    args = {
        "action_type": "create_task",
        "title": "Implement Bedrock Nova provider",
        "project_id": "Personal OS",
        "due_date": "tomorrow",
        "priority": 1,
    }
    res = await executor.execute_tool("propose_task_action", args)
    assert res["status"] == "proposal_generated"
    assert res["requires_confirmation"] is False
    prop = res["proposal"]
    assert prop["action_type"] == "create_task"
    assert prop["parameters"]["title"] == "Implement Bedrock Nova provider"
    assert prop["parameters"]["project_id"] == "p1"
    assert "2026-10-21" in prop["parameters"]["due_date"]


@pytest.mark.asyncio
async def test_propose_task_action_delete_enforces_confirmation(
    executor: PersonalOSToolExecutor, mock_task_service: MagicMock
) -> None:
    task = Task(id="t1", owner_id="user_123", title="Old temporary note", status="todo", revision=3)
    mock_task_service.get_task.return_value = task

    args = {
        "action_type": "delete_task",
        "task_id": "t1",
        "expected_revision": 3,
    }
    res = await executor.execute_tool("propose_task_action", args)
    assert res["status"] == "proposal_generated"
    # Delete MUST require confirmation
    assert res["requires_confirmation"] is True
    assert res["proposal"]["requires_confirmation"] is True
    assert "DELETE task" in res["diff_summary"]


@pytest.mark.asyncio
async def test_propose_task_action_conflict_detection(
    executor: PersonalOSToolExecutor, mock_task_service: MagicMock
) -> None:
    # Current revision is 5, but proposal passed 4
    task = Task(id="t1", owner_id="user_123", title="Important doc", status="todo", revision=5)
    mock_task_service.get_task.return_value = task

    args = {
        "action_type": "update_task",
        "task_id": "t1",
        "expected_revision": 4,
        "title": "New title",
    }
    res = await executor.execute_tool("propose_task_action", args)
    assert res["status"] == "conflict"
    assert "Expected revision 4, but current is 5" in res["error"]


@pytest.mark.asyncio
async def test_apply_proposal_requires_confirmation_refusal(
    executor: PersonalOSToolExecutor,
) -> None:
    proposal = {
        "action_type": "delete_task",
        "parameters": {"task_id": "t1", "expected_revision": 1},
        "requires_confirmation": True,
    }
    res = await executor.apply_proposal(proposal, confirmed=False)
    assert res["status"] == "confirmation_required"
    assert "requires explicit user confirmation" in res["message"]


@pytest.mark.asyncio
async def test_apply_proposal_confirmed_execution(
    executor: PersonalOSToolExecutor, mock_task_service: MagicMock
) -> None:
    proposal = {
        "action_type": "delete_task",
        "parameters": {"task_id": "t1", "expected_revision": 1},
        "requires_confirmation": True,
    }
    res = await executor.apply_proposal(proposal, confirmed=True)
    assert res["status"] == "applied"
    mock_task_service.delete_task.assert_awaited_once_with("user_123", "t1", expected_revision=1)
