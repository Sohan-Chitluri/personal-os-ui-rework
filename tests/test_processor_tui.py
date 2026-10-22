import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from personal_os.core.processor import BackgroundProcessor, KnowledgeApplier
from personal_os.core.vault import VaultManager, VaultConfig


@pytest.fixture
def test_vault(tmp_path: Path):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    return VaultManager(VaultConfig(root=vault_dir))


def test_task_matching_avoids_short_words_and_stop_words():
    """Finding #3: Test that common letters/stop words don't match arbitrary tasks."""
    lines = [
        "- [ ] Implement authentication flow",
        "- [ ] Write unit tests for database",
        "- [ ] Refactor navigation sidebar",
    ]

    # Stop words and single letters should NOT match
    assert KnowledgeApplier._find_task_to_complete(lines, "a is done") is None
    assert KnowledgeApplier._find_task_to_complete(lines, "the is done") is None
    assert KnowledgeApplier._find_task_to_complete(lines, "the active task is done") is None
    assert KnowledgeApplier._find_task_to_complete(lines, "i finished it") is None


def test_task_matching_avoids_ambiguous_completions():
    """Finding #3: If multiple tasks share the same keywords, do not silently complete one."""
    lines = [
        "- [ ] Deploy auth service",
        "- [ ] Deploy billing service",
    ]

    # "Deploy service done" matches both lines equally well -> ambiguous
    assert KnowledgeApplier._find_task_to_complete(lines, "Deploy service is done") is None

    # Specific task description matches unambiguously
    assert KnowledgeApplier._find_task_to_complete(lines, "Deploy auth service is done") == 0
    assert KnowledgeApplier._find_task_to_complete(lines, "billing service is done") == 1


def test_action_and_apply_mark_complete(test_vault: VaultManager):
    """Finding #3: Test that _action_mark_complete and _apply_completion mark correct task."""
    project_dir = test_vault.config.projects_dir / "Alpha"
    project_dir.mkdir(parents=True)
    tasks_file = project_dir / "Tasks.md"
    tasks_file.write_text("- [ ] Setup CI pipeline\n- [ ] Fix navigation crash\n")

    applier = KnowledgeApplier(test_vault)

    # False match attempts do not alter the file
    res = applier._apply_completion({"project": "Alpha", "description": "a done"})
    assert "No matching task" in res[0]
    assert "- [ ] Setup CI pipeline" in tasks_file.read_text()

    # Exact match completes the task
    action = MagicMock()
    action.project = "Alpha"
    action.description = "Fix navigation crash is done"
    res = applier._action_mark_complete(action)
    assert "Marked complete" in res[0]

    updated = tasks_file.read_text().splitlines()
    assert updated[0] == "- [ ] Setup CI pipeline"
    assert updated[1] == "- [x] Fix navigation crash"


@pytest.mark.asyncio
async def test_sync_callback_supported_in_background_processor(test_vault: VaultManager):
    """Finding #4: A synchronous callback must not raise TypeError when awaited."""
    callback_called = False
    received_text = None

    def sync_callback(result, text, source):
        nonlocal callback_called, received_text
        callback_called = True
        received_text = text

    processor = BackgroundProcessor(test_vault, on_processed=sync_callback)
    await processor.start()

    try:
        # Enqueue item
        await processor.queue("Simple journal entry", source="test")
        await asyncio.wait_for(processor._queue.join(), timeout=2.0)
        await asyncio.sleep(0.05)

        assert callback_called is True
        assert received_text == "Simple journal entry"
    finally:
        await processor.stop()


@pytest.mark.asyncio
async def test_async_callback_supported_in_background_processor(test_vault: VaultManager):
    """Finding #4: An asynchronous callback must also be awaited properly."""
    callback_called = False

    async def async_callback(result, text, source):
        nonlocal callback_called
        await asyncio.sleep(0.01)
        callback_called = True

    processor = BackgroundProcessor(test_vault, on_processed=async_callback)
    await processor.start()

    try:
        await processor.queue("Another entry", source="test")
        await asyncio.wait_for(processor._queue.join(), timeout=2.0)
        await asyncio.sleep(0.05)
        assert callback_called is True
    finally:
        await processor.stop()
