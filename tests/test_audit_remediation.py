"""Regression tests verifying remediation of findings from the 2026-10-25 Ponytail audit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from personal_os.core.capture import capture_and_process
from personal_os.core.config import VaultConfig
from personal_os.core.processor import BackgroundProcessor, KnowledgeExtractor
from personal_os.core.search import SearchEngine
from personal_os.core.vault import VaultManager
from personal_os.core.weekly_review import WeeklyReviewGenerator

# --------------------------------------------------------------------------- #
# Finding 2: Search fallback matches journal Markdown files
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_finding_2_search_python_matches_journal(tmp_path):
    """Verify search_python matches journal entries when type:journal filter is used."""
    vm = VaultManager(VaultConfig(root=tmp_path))
    journal_file = vm.config.journal_dir / "2026-03-29.md"
    journal_file.parent.mkdir(parents=True, exist_ok=True)
    journal_file.write_text("# 2026-03-29\n\nUniqueKeywordInJournal\n", encoding="utf-8")

    tasks_file = vm.config.projects_dir / "Alpha" / "Tasks.md"
    tasks_file.parent.mkdir(parents=True, exist_ok=True)
    tasks_file.write_text("# Tasks\n\nUniqueKeywordInJournal\n", encoding="utf-8")

    engine = SearchEngine(vm)
    filters = engine.parse_query("UniqueKeywordInJournal type:journal")
    results = await engine.search_python(filters)
    files = [r.file.replace("\\", "/") for r in results]
    assert any("2026-03-29.md" in f for f in files)
    assert not any("Tasks.md" in f for f in files)


# --------------------------------------------------------------------------- #
# Finding 3: Dry-run does not mutate files or git state
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_finding_3_dry_run_does_not_mutate(tmp_path):
    """Verify BackgroundProcessor.process with dry_run=True does not write files or commits."""
    import git

    repo = git.Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "test@test.com").release()

    vm = VaultManager(VaultConfig(root=tmp_path))
    proj_dir = vm.create_project("Maestro")
    tasks_path = proj_dir / "Tasks.md"
    initial_content = tasks_path.read_text(encoding="utf-8")
    initial_commits = [c.hexsha for c in repo.iter_commits()]

    processor = BackgroundProcessor(vm)
    result = await processor.process("need to write documentation", source="Reprocess", dry_run=True)

    assert any("[dry-run]" in change for change in result.changes_made)
    assert tasks_path.read_text(encoding="utf-8") == initial_content
    assert [c.hexsha for c in repo.iter_commits()] == initial_commits


# --------------------------------------------------------------------------- #
# Finding 4: Reprocessing does not duplicate extracted tasks or progress
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_finding_4_reprocessing_does_not_duplicate_tasks(tmp_path):
    """Verify processing the same text twice does not duplicate tasks in Tasks.md."""
    vm = VaultManager(VaultConfig(root=tmp_path))
    vm.create_project("Maestro")
    processor = BackgroundProcessor(vm)

    entry_text = "need to update firmware for maestro"
    res1 = await processor.process(entry_text, source="Capture")
    assert any("Added task" in c for c in res1.changes_made)

    res2 = await processor.process(entry_text, source="Reprocess")
    assert any("already exists" in c for c in res2.changes_made)

    tasks_content = (vm.config.projects_dir / "Maestro" / "Tasks.md").read_text(encoding="utf-8")
    assert tasks_content.count("update firmware") == 1


# --------------------------------------------------------------------------- #
# Finding 5: Capture propagates capture_id to processor
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_finding_5_capture_propagates_capture_id(tmp_path, monkeypatch):
    """Verify capture_and_process explicitly forwards capture_id to process_entry."""
    vm = VaultManager(VaultConfig(root=tmp_path))
    captured_cid = None

    async def mock_process(text: str, source: str = "Text", capture_id: str = "", dry_run: bool = False):
        nonlocal captured_cid
        captured_cid = capture_id
        from personal_os.core.processor import ProcessingResult
        return ProcessingResult()

    monkeypatch.setattr("personal_os.core.capture.process_entry", mock_process)

    receipt, _ = await capture_and_process("Test note for capture_id", vault=vm)
    assert receipt.persisted
    assert receipt.capture_id != ""
    assert captured_cid == receipt.capture_id


# --------------------------------------------------------------------------- #
# Finding 7: Weekly review date bounds and undated historical handling
# --------------------------------------------------------------------------- #

def test_finding_7_weekly_review_filters_by_date(tmp_path):
    """Verify collect_completed_tasks respects monday/sunday bounds and handles undated tasks."""
    vm = VaultManager(VaultConfig(root=tmp_path))
    proj_dir = vm.config.projects_dir / "Beta"
    proj_dir.mkdir(parents=True, exist_ok=True)
    tasks_file = proj_dir / "Tasks.md"
    tasks_file.write_text(
        "# Tasks\n\n"
        "## Completed Tasks\n"
        "- [x] **2026-03-25**: Task done inside week\n"
        "- [x] [completed: 2026-03-10] Task done previous week\n"
        "- [x] 📅 2026-04-15 Task done future week\n"
        "- [x] Undated completed task\n",
        encoding="utf-8",
    )

    gen = WeeklyReviewGenerator(vault=vm)
    monday = datetime(2026, 3, 23)
    sunday = datetime(2026, 3, 29)

    # Default include_undated=True: inside week + undated
    completed = gen.collect_completed_tasks(monday, sunday, include_undated=True)
    texts = [t["task"] for t in completed]
    assert any("Task done inside week" in t for t in texts)
    assert any("Undated completed task" in t for t in texts)
    assert not any("Task done previous week" in t for t in texts)
    assert not any("Task done future week" in t for t in texts)

    # Strict include_undated=False: inside week only
    strict_completed = gen.collect_completed_tasks(monday, sunday, include_undated=False)
    strict_texts = [t["task"] for t in strict_completed]
    assert any("Task done inside week" in t for t in strict_texts)
    assert not any("Undated completed task" in t for t in strict_texts)


# --------------------------------------------------------------------------- #
# Finding 8: Project creation creates a single atomic git commit
# --------------------------------------------------------------------------- #

def test_finding_8_project_creation_is_single_atomic_commit(tmp_path):
    """Verify create_project generates exactly 1 git commit for all template files."""
    import git

    repo = git.Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "test@test.com").release()

    initial_file = tmp_path / "init.txt"
    initial_file.write_text("init", encoding="utf-8")
    repo.index.add(["init.txt"])
    repo.index.commit("Initial commit")

    initial_commit_count = len(list(repo.iter_commits()))

    vm = VaultManager(VaultConfig(root=tmp_path))
    vm.create_project("SingleCommitProject")

    commits = list(repo.iter_commits())
    assert len(commits) == initial_commit_count + 1
    assert commits[0].message.startswith("Create project: SingleCommitProject")


# --------------------------------------------------------------------------- #
# Finding 9: Project health BLOCKED state is not overwritten by TODO
# --------------------------------------------------------------------------- #

def test_finding_9_project_health_blocked_not_overwritten_by_todo(tmp_path):
    """Verify red health from BLOCKED in Architecture.md is not downgraded by TODO in README.md."""
    vm = VaultManager(VaultConfig(root=tmp_path))
    proj_dir = vm.config.projects_dir / "HealthTest"
    proj_dir.mkdir(parents=True, exist_ok=True)

    (proj_dir / "Architecture.md").write_text("# Arch\n\nBLOCKED on vendor chip.", encoding="utf-8")
    (proj_dir / "README.md").write_text("# Readme\n\nTODO: write tests.\n", encoding="utf-8")

    meta = vm._read_project_meta(proj_dir)
    assert meta["health"] == "red"
    assert meta["status"] == "blocked"


# --------------------------------------------------------------------------- #
# Finding 11: GitHub token decryption failure raises GitHubAuthError
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_finding_11_github_invalid_token_decryption_raises_github_auth_error():
    """Verify decryption failure produces an actionable GitHubAuthError rather than bubbling a 500 error."""
    from personal_os.config import Settings
    from personal_os.db.models import UserGitHubAuth
    from personal_os.services.github import GitHubAuthError, GitHubService

    settings = Settings()
    auth = UserGitHubAuth(
        user_id="user-123",
        encrypted_token="bad-ciphertext-token",
        encrypted_refresh_token="bad-ciphertext-refresh",
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = auth
    mock_session.execute.return_value = mock_result

    service = GitHubService(session=mock_session, settings=settings)

    with pytest.raises(GitHubAuthError) as exc_info:
        await service.get_client("user-123")
    assert "could not be decrypted" in str(exc_info.value).lower()

    with pytest.raises(GitHubAuthError) as exc_info2:
        await service.refresh_user_token(auth)
    assert "could not be decrypted" in str(exc_info2.value).lower()


# --------------------------------------------------------------------------- #
# Finding 12: Companion heartbeat task is cleaned up on disconnect
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_finding_12_companion_heartbeat_task_cleaned_on_disconnect(monkeypatch):
    """Verify heartbeat task is removed from daemon._tasks on disconnect/reconnect."""
    import websockets

    from personal_os.companion.config import CompanionSettings
    from personal_os.companion.daemon import CompanionDaemon

    settings = CompanionSettings()
    daemon = CompanionDaemon(settings=settings)

    class DummyWs:
        async def send(self, data): pass
        async def recv(self):
            from personal_os.companion.protocol import RPCResponse
            return RPCResponse(id="1", result={"authenticated": True}).model_dump_json()

    monkeypatch.setattr(daemon, "_perform_handshake", AsyncMock(return_value=True))

    async def mock_receive_loop(ws):
        return

    monkeypatch.setattr(daemon, "_receive_loop", mock_receive_loop)
    monkeypatch.setattr(daemon, "_sleep_with_backoff", AsyncMock())

    class MockConnectContext:
        async def __aenter__(self): return DummyWs()
        async def __aexit__(self, *args): pass

    monkeypatch.setattr(websockets, "connect", lambda *args, **kwargs: MockConnectContext())

    assert len(daemon._tasks) == 0
    await daemon._run_connection_lifecycle()
    assert len(daemon._tasks) == 0


# --------------------------------------------------------------------------- #
# Finding 13: Project matching uses word boundaries and vault existence
# --------------------------------------------------------------------------- #

def test_finding_13_project_matcher_handles_router_and_word_boundaries(tmp_path):
    """Verify ordinary words like 'router' do not match Maestro, and word boundaries prevent substring matches."""
    vm = VaultManager(VaultConfig(root=tmp_path))
    vm.create_project("Maestro")
    vm.create_project("Team-NAMO")

    extractor = KnowledgeExtractor(vm)

    # "router" must NOT resolve to Maestro
    assert extractor._identify_project("I rebooted the wifi router this morning") is None

    # "Maestro" matches Maestro
    assert extractor._identify_project("Worked on Maestro audio engine") == "Maestro"

    # "Team-NAMO" matches Team-NAMO
    assert extractor._identify_project("Meeting with Team-NAMO about robot") == "Team-NAMO"

    # Substring of project must not false match: e.g. "Drone" should not match "droned"
    vm.create_project("Drone")
    assert extractor._identify_project("The speaker droned on for hours") is None
    assert extractor._identify_project("Flew the Drone today") == "Drone"
