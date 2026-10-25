"""Regression tests for OpenRouter LLM provider parsing."""

import pytest

from personal_os.core.extraction.models import ExtractionType
from personal_os.core.llm_openrouter import OpenRouterProvider


@pytest.fixture
def provider():
    return OpenRouterProvider(api_key="test-key", model="test-model")


def test_parse_titles_with_brackets_and_wip(provider):
    raw = (
        '[\n'
        '  {"type": "task", "description": "Fix [bug] in auth module", "certainty": 0.95},\n'
        '  {"type": "task", "description": "Implement [WIP] payment flow", "certainty": 0.8}\n'
        ']'
    )
    results = provider._parse(raw)
    assert len(results) == 2
    assert results[0].type == ExtractionType.TASK
    assert results[0].description == "Fix [bug] in auth module"
    assert results[1].description == "Implement [WIP] payment flow"


def test_parse_markdown_links_in_description(provider):
    raw = (
        '[\n'
        '  {"type": "task", "description": "Review PR [docs](https://github.com/org/repo/pull/42)"}\n'
        ']'
    )
    results = provider._parse(raw)
    assert len(results) == 1
    assert results[0].description == "Review PR [docs](https://github.com/org/repo/pull/42)"


def test_parse_nested_json_structures(provider):
    raw = (
        '[\n'
        '  {\n'
        '    "type": "task",\n'
        '    "description": "Task with nested arrays",\n'
        '    "tags": ["backend", "urgent"],\n'
        '    "nested": {"items": [1, 2, 3]}\n'
        '  }\n'
        ']'
    )
    results = provider._parse(raw)
    assert len(results) == 1
    assert results[0].description == "Task with nested arrays"


def test_parse_escaped_quotes_and_brackets(provider):
    raw = (
        '[\n'
        '  {"type": "task", "description": "Said \\"hello [world]\\" to [team]"}\n'
        ']'
    )
    results = provider._parse(raw)
    assert len(results) == 1
    assert results[0].description == 'Said "hello [world]" to [team]'


def test_parse_fenced_json_with_surrounding_text_and_bracketed_notes(provider):
    raw = (
        'Note [1]: Here is the extraction from your daily notes [2]:\n'
        '```json\n'
        '[\n'
        '  {"type": "task", "description": "Check telemetry [hotfix]"}\n'
        ']\n'
        '```\n'
        'Please verify reference [3].'
    )
    results = provider._parse(raw)
    assert len(results) == 1
    assert results[0].description == "Check telemetry [hotfix]"


def test_parse_malformed_items_preserves_valid_items(provider):
    raw = (
        '[\n'
        '  {"type": "task", "description": "Valid task 1"},\n'
        '  "not a dictionary",\n'
        '  {"type": "task", "description": ""},\n'
        '  {"type": "task", "description": "Valid task 2"}\n'
        ']'
    )
    results = provider._parse(raw)
    assert len(results) == 2
    assert results[0].description == "Valid task 1"
    assert results[1].description == "Valid task 2"


def test_parse_malformed_unparseable_output_fails_safely(provider):
    raw = "I am an AI and cannot extract any tasks from this text: [error: invalid prompt]."
    results = provider._parse(raw)
    assert results == []


def test_parse_empty_array(provider):
    raw = "[]"
    results = provider._parse(raw)
    assert results == []
