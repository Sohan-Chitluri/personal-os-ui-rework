"""Unit and integration tests for BedrockAssistantEngine multi-turn conversation and tool loop."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from personal_os.config import Settings
from personal_os.core.llm import (
    ChatMessage,
    ChatResponse,
    LLMProvider,
    TokenUsage,
    ToolCall,
)
from personal_os.db.models import Task
from personal_os.services.assistant import BedrockAssistantEngine
from personal_os.services.interfaces import ProjectService, TaskService


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 10, 20, 14, 30, 0, tzinfo=UTC)


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
    return service


@pytest.mark.asyncio
async def test_assistant_simple_conversation(
    mock_task_service: MagicMock, mock_project_service: MagicMock, fixed_now: datetime
) -> None:
    mock_provider = MagicMock(spec=LLMProvider)
    mock_provider.chat = AsyncMock(
        return_value=ChatResponse(
            message=ChatMessage(role="assistant", content="Hello! How can I help you today?"),
            stop_reason="end_turn",
            usage=TokenUsage(input_tokens=20, output_tokens=10, total_tokens=30),
            latency_ms=120.0,
            model="us.amazon.nova-2-lite-v1:0",
        )
    )

    settings = Settings(environment="test")
    engine = BedrockAssistantEngine(provider=mock_provider, settings=settings)

    result = await engine.run_turn(
        owner_id="user_1",
        conversation_history=[],
        new_user_message="Hello!",
        task_service=mock_task_service,
        project_service=mock_project_service,
        ref_time=fixed_now,
    )

    assert result.reply == "Hello! How can I help you today?"
    assert result.tokens_used == 30
    assert result.action_proposals == []
    assert result.applied_actions == []


@pytest.mark.asyncio
async def test_assistant_multi_turn_tool_calling(
    mock_task_service: MagicMock, mock_project_service: MagicMock, fixed_now: datetime
) -> None:
    # Step 1: Model calls search_tasks
    turn1_response = ChatResponse(
        message=ChatMessage(
            role="assistant",
            content="Checking your tasks...",
            tool_calls=[
                ToolCall(id="c1", name="search_tasks", arguments={"query": "review"})
            ],
        ),
        stop_reason="tool_use",
        usage=TokenUsage(input_tokens=40, output_tokens=15, total_tokens=55),
        latency_ms=100.0,
        model="us.amazon.nova-2-lite-v1:0",
    )

    # Step 2: After receiving search results, model answers
    turn2_response = ChatResponse(
        message=ChatMessage(
            role="assistant",
            content="You have 1 task matching 'review': Prepare weekly review.",
        ),
        stop_reason="end_turn",
        usage=TokenUsage(input_tokens=60, output_tokens=20, total_tokens=80),
        latency_ms=150.0,
        model="us.amazon.nova-2-lite-v1:0",
    )

    mock_provider = MagicMock(spec=LLMProvider)
    mock_provider.chat = AsyncMock(side_effect=[turn1_response, turn2_response])

    t1 = Task(id="t1", owner_id="user_1", title="Prepare weekly review", status="todo", revision=1)
    mock_task_service.list_tasks.return_value = [t1]

    settings = Settings(environment="test")
    engine = BedrockAssistantEngine(provider=mock_provider, settings=settings)

    result = await engine.run_turn(
        owner_id="user_1",
        conversation_history=[],
        new_user_message="Do I have any tasks about review?",
        task_service=mock_task_service,
        project_service=mock_project_service,
        ref_time=fixed_now,
    )

    assert mock_provider.chat.await_count == 2
    assert "You have 1 task matching 'review'" in result.reply
    assert result.tokens_used == 135


@pytest.mark.asyncio
async def test_assistant_creates_and_auto_applies_task(
    mock_task_service: MagicMock, mock_project_service: MagicMock, fixed_now: datetime
) -> None:
    # Step 1: Model proposes create_task
    turn1_response = ChatResponse(
        message=ChatMessage(
            role="assistant",
            content="Creating the task for you...",
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="propose_task_action",
                    arguments={
                        "action_type": "create_task",
                        "title": "Buy groceries",
                        "due_date": "tomorrow",
                    },
                )
            ],
        ),
        stop_reason="tool_use",
        usage=TokenUsage(input_tokens=50, output_tokens=25, total_tokens=75),
    )

    # Step 2: Model confirms creation
    turn2_response = ChatResponse(
        message=ChatMessage(
            role="assistant",
            content="I have created the task **Buy groceries** for tomorrow.",
        ),
        stop_reason="end_turn",
        usage=TokenUsage(input_tokens=80, output_tokens=20, total_tokens=100),
    )

    mock_provider = MagicMock(spec=LLMProvider)
    mock_provider.chat = AsyncMock(side_effect=[turn1_response, turn2_response])

    created_task = Task(
        id="t_new",
        owner_id="user_1",
        title="Buy groceries",
        status="todo",
        revision=1,
    )
    mock_task_service.create_task.return_value = created_task

    settings = Settings(environment="test")
    engine = BedrockAssistantEngine(provider=mock_provider, settings=settings)

    result = await engine.run_turn(
        owner_id="user_1",
        conversation_history=[],
        new_user_message="Add task Buy groceries tomorrow",
        task_service=mock_task_service,
        project_service=mock_project_service,
        ref_time=fixed_now,
        auto_apply_safe_proposals=True,
    )

    assert mock_task_service.create_task.awaited
    assert len(result.action_proposals) == 1
    assert result.action_proposals[0]["action_type"] == "create_task"
    assert len(result.applied_actions) == 1
    assert result.applied_actions[0]["task_id"] == "t_new"
