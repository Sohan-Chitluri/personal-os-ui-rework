"""Unit tests for BedrockNovaProvider and Bedrock Converse integration."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from personal_os.config import Settings
from personal_os.core.extraction.models import ExtractionType
from personal_os.core.llm import (
    ChatMessage,
    LLMProviderError,
    ToolCall,
    ToolDefinition,
    get_provider,
)
from personal_os.core.llm_bedrock import BedrockNovaProvider, calculate_nova_cost


def test_calculate_nova_cost_us_geo() -> None:
    # 1,000,000 input tokens = $0.33, 1,000,000 output tokens = $2.75
    cost = calculate_nova_cost("us.amazon.nova-2-lite-v1:0", 1_000_000, 1_000_000)
    assert abs(cost - 3.08) < 1e-4

    # 10,000 input, 2,000 output
    cost_small = calculate_nova_cost("us.amazon.nova-2-lite-v1:0", 10_000, 2_000)
    expected = (10_000 * 0.33 + 2_000 * 2.75) / 1_000_000
    assert abs(cost_small - expected) < 1e-6


def test_calculate_nova_cost_global() -> None:
    cost = calculate_nova_cost("global.amazon.nova-2-lite-v1:0", 1_000_000, 1_000_000)
    assert abs(cost - 2.80) < 1e-4


def test_message_formatting_merges_consecutive_roles() -> None:
    provider = BedrockNovaProvider()
    messages = [
        ChatMessage(role="user", content="First user message"),
        ChatMessage(role="user", content="Second user message"),
        ChatMessage(role="assistant", content="Assistant reply"),
    ]
    formatted = provider._format_messages_for_converse(messages)
    assert len(formatted) == 2
    assert formatted[0]["role"] == "user"
    assert len(formatted[0]["content"]) == 2
    assert formatted[0]["content"][0]["text"] == "First user message"
    assert formatted[0]["content"][1]["text"] == "Second user message"
    assert formatted[1]["role"] == "assistant"
    assert formatted[1]["content"][0]["text"] == "Assistant reply"


def test_message_formatting_tool_calls_and_results() -> None:
    provider = BedrockNovaProvider()
    messages = [
        ChatMessage(role="user", content="Find my tasks"),
        ChatMessage(
            role="assistant",
            content="Searching...",
            tool_calls=[ToolCall(id="call_1", name="search_tasks", arguments={"query": "test"})],
        ),
        ChatMessage(
            role="tool", content='{"tasks": []}', tool_call_id="call_1", name="search_tasks"
        ),
    ]
    formatted = provider._format_messages_for_converse(messages)
    assert len(formatted) == 3

    # Assistant message contains text and toolUse
    asst_blocks = formatted[1]["content"]
    assert len(asst_blocks) == 2
    assert asst_blocks[0]["text"] == "Searching..."
    assert asst_blocks[1]["toolUse"]["toolUseId"] == "call_1"
    assert asst_blocks[1]["toolUse"]["name"] == "search_tasks"
    assert asst_blocks[1]["toolUse"]["input"] == {"query": "test"}

    # Tool message is mapped to user role with toolResult block
    tool_blocks = formatted[2]["content"]
    assert formatted[2]["role"] == "user"
    assert "toolResult" in tool_blocks[0]
    assert tool_blocks[0]["toolResult"]["toolUseId"] == "call_1"
    assert tool_blocks[0]["toolResult"]["content"][0]["text"] == '{"tasks": []}'


def test_tools_formatting_bedrock_spec() -> None:
    provider = BedrockNovaProvider()
    tools = [
        ToolDefinition(
            name="search_tasks",
            description="Search tasks by query",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        )
    ]
    specs = provider._format_tools_for_converse(tools)
    assert len(specs) == 1
    spec = specs[0]["toolSpec"]
    assert spec["name"] == "search_tasks"
    assert spec["description"] == "Search tasks by query"
    expected_schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    assert spec["inputSchema"]["json"] == expected_schema


@pytest.mark.asyncio
async def test_chat_success() -> None:
    mock_boto = MagicMock()
    mock_boto.converse.return_value = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": "Hello, I am Raphael!"}],
            }
        },
        "stopReason": "end_turn",
        "usage": {
            "inputTokens": 45,
            "outputTokens": 12,
            "totalTokens": 57,
        },
    }

    provider = BedrockNovaProvider(boto3_client=mock_boto)
    response = await provider.chat(
        messages=[ChatMessage(role="user", content="Hi")],
        system_prompt="You are Raphael.",
    )

    assert response.message.content == "Hello, I am Raphael!"
    assert response.message.role == "assistant"
    assert response.stop_reason == "end_turn"
    assert response.usage.input_tokens == 45
    assert response.usage.output_tokens == 12
    assert response.usage.total_tokens == 57
    assert response.latency_ms > 0
    assert response.estimated_cost_usd > 0


@pytest.mark.asyncio
async def test_chat_tool_use_response() -> None:
    mock_boto = MagicMock()
    mock_boto.converse.return_value = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"text": "Let me look up your projects."},
                    {
                        "toolUse": {
                            "toolUseId": "tu_123",
                            "name": "list_projects",
                            "input": {"status": "active"},
                        }
                    },
                ],
            }
        },
        "stopReason": "tool_use",
        "usage": {"inputTokens": 80, "outputTokens": 25, "totalTokens": 105},
    }

    provider = BedrockNovaProvider(boto3_client=mock_boto)
    response = await provider.chat(
        messages=[ChatMessage(role="user", content="What projects do I have?")],
    )

    assert response.message.content == "Let me look up your projects."
    assert len(response.message.tool_calls) == 1
    tc = response.message.tool_calls[0]
    assert tc.id == "tu_123"
    assert tc.name == "list_projects"
    assert tc.arguments == {"status": "active"}
    assert response.stop_reason == "tool_use"


@pytest.mark.asyncio
async def test_chat_client_error_raises_llm_provider_error() -> None:
    mock_boto = MagicMock()
    err_response = {"Error": {"Code": "ValidationException", "Message": "Operation not allowed"}}
    mock_boto.converse.side_effect = ClientError(err_response, "Converse")

    provider = BedrockNovaProvider(boto3_client=mock_boto)
    with pytest.raises(LLMProviderError, match="Bedrock Converse error \\[ValidationException\\]"):
        await provider.chat(messages=[ChatMessage(role="user", content="Hi")])


@pytest.mark.asyncio
async def test_chat_stream() -> None:
    mock_boto = MagicMock()
    events = [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockDelta": {"delta": {"text": "Hello "}}},
        {"contentBlockDelta": {"delta": {"text": "world!"}}},
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}}},
    ]
    mock_boto.converse_stream.return_value = {"stream": iter(events)}

    provider = BedrockNovaProvider(boto3_client=mock_boto)
    collected_text = []
    async for chunk in provider.chat_stream([ChatMessage(role="user", content="Hello")]):
        if chunk.delta_text:
            collected_text.append(chunk.delta_text)

    assert "".join(collected_text) == "Hello world!"


@pytest.mark.asyncio
async def test_extract_parses_semantic_items() -> None:
    sample_json = [
        {
            "type": "task",
            "description": "Prepare slides for the review",
            "project": "VLSI",
            "certainty": 0.95,
            "temporal_context": "tomorrow",
            "confidence": 0.9,
            "source_excerpt": "I need to prepare slides for the review tomorrow",
        },
        {
            "type": "progress",
            "description": "Fixed the clock skew",
            "project": "VLSI",
            "certainty": 0.85,
            "confidence": 0.88,
            "source_excerpt": "Clock skew is now fixed",
        },
    ]

    mock_boto = MagicMock()
    mock_boto.converse.return_value = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": f"```json\n{json.dumps(sample_json)}\n```"}],
            }
        },
        "stopReason": "end_turn",
        "usage": {"inputTokens": 100, "outputTokens": 50, "totalTokens": 150},
    }

    provider = BedrockNovaProvider(boto3_client=mock_boto)
    capture_text = (
        "I need to prepare slides for the review tomorrow. Clock skew is now fixed."
    )
    extractions = await provider.extract(capture_text)

    assert len(extractions) == 2
    assert extractions[0].type == ExtractionType.TASK
    assert extractions[0].description == "Prepare slides for the review"
    assert extractions[0].project_ref is not None
    assert extractions[0].project_ref.name == "VLSI"
    assert extractions[0].deadline is not None
    assert extractions[0].deadline.raw_text == "tomorrow"
    assert extractions[1].type == ExtractionType.PROGRESS
    assert extractions[1].description == "Fixed the clock skew"


def test_factory_get_provider_bedrock() -> None:
    settings = Settings(
        llm_provider="bedrock",
        bedrock_region="us-east-1",
        bedrock_model_id="us.amazon.nova-2-lite-v1:0",
        bedrock_timeout_seconds=45.0,
        bedrock_max_tokens=2048,
        bedrock_temperature=0.2,
    )
    provider = get_provider(settings)
    assert isinstance(provider, BedrockNovaProvider)
    assert provider.region_name == "us-east-1"
    assert provider.model_id == "us.amazon.nova-2-lite-v1:0"
    assert provider.timeout_seconds == 45.0
    assert provider.max_tokens == 2048
    assert provider.temperature == 0.2
