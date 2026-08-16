"""Tests for T0-B Batch Vault Commits.

Verifies:
- Multiple processing actions batched into single commit
- Atomicity: failure rolls back entire batch
- Journal capture (persist-first) remains immediate commit
- Existing single-write callers still work
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from personal_os.core.vault import VaultManager, VaultConfig
from personal_os.core.processor import BackgroundProcessor, KnowledgeApplier
from personal_os.core.extraction.actions import (
    CreateTask,
    RecordProgress,
    RecordActivity,
    RecordProblem,
    AddDecision,
    NoOp,
)


class TestBatchCommits:
    """Test batch commit functionality in VaultManager."""
    
    @pytest.fixture
    def temp_vault(self, tmp_path):
        """Create a temporary vault for testing."""
        vault_root = tmp_path / "vault"
        fake_home = tmp_path / "home"
        fake_home.mkdir(parents=True, exist_ok=True)
        
        import os
        os.environ['HOME'] = str(fake_home)
        os.environ['PERSONAL_OS_VAULT'] = str(vault_root)
        os.environ['GIT_AUTHOR_NAME'] = "Test"
        os.environ['GIT_AUTHOR_EMAIL'] = "test@test.local"
        os.environ['GIT_COMMITTER_NAME'] = "Test"
        os.environ['GIT_COMMITTER_EMAIL'] = "test@test.local"
        
        # Reset singletons
        import personal_os.core.config as config
        import personal_os.core.vault as vault
        config._config = None
        vault._vault = None
        
        from personal_os.core.config import get_config
        cfg = get_config()
        
        vm = VaultManager(cfg.vault)
        return vm
    
    def test_batch_write_file_single_commit(self, temp_vault):
        """Multiple write_file calls in batch produce single commit."""
        vm = temp_vault
        
        # Create test files in batch
        vm.begin_batch("Test batch")
        vm.write_file(vm.config.root / "file1.md", "Content 1")
        vm.write_file(vm.config.root / "file2.md", "Content 2")
        vm.write_file(vm.config.root / "file3.md", "Content 3")
        vm.commit_batch()
        
        # Verify only one commit was made for the batch
        commits = list(vm._repo.iter_commits())
        batch_commits = [c for c in commits if "Test batch" in c.message]
        assert len(batch_commits) == 1, f"Expected 1 batch commit, got {len(batch_commits)}"
        
        # Verify all files exist
        assert (vm.config.root / "file1.md").read_text() == "Content 1"
        assert (vm.config.root / "file2.md").read_text() == "Content 2"
        assert (vm.config.root / "file3.md").read_text() == "Content 3"
    
    def test_batch_append_file_single_commit(self, temp_vault):
        """Multiple append_file calls in batch produce single commit."""
        vm = temp_vault
        
        # Create initial file
        vm.write_file(vm.config.root / "existing.md", "Initial\n")
        
        # Append in batch
        vm.begin_batch("Append batch")
        vm.append_file(vm.config.root / "existing.md", "Append 1\n")
        vm.append_file(vm.config.root / "existing.md", "Append 2\n")
        vm.commit_batch()
        
        # Verify single batch commit
        commits = list(vm._repo.iter_commits())
        batch_commits = [c for c in commits if "Append batch" in c.message]
        assert len(batch_commits) == 1
        
        # Verify content
        content = (vm.config.root / "existing.md").read_text()
        assert "Initial" in content
        assert "Append 1" in content
        assert "Append 2" in content
    
    def test_batch_rollback_discards_changes(self, temp_vault):
        """Rollback discards all staged changes."""
        vm = temp_vault
        
        vm.begin_batch("To be rolled back")
        vm.write_file(vm.config.root / "to_remove.md", "Will be removed")
        vm.rollback_batch()
        
        # File should not exist
        assert not (vm.config.root / "to_remove.md").exists()
        
        # No commit for the batch
        commits = list(vm._repo.iter_commits())
        batch_commits = [c for c in commits if "rolled back" in c.message.lower()]
        assert len(batch_commits) == 0
    
    def test_nested_batch_raises_error(self, temp_vault):
        """Starting a batch while one is active raises error."""
        vm = temp_vault
        
        vm.begin_batch("First batch")
        with pytest.raises(RuntimeError, match="Batch already in progress"):
            vm.begin_batch("Second batch")
        vm.rollback_batch()  # Clean up
    
    def test_commit_without_batch_raises_error(self, temp_vault):
        """Calling commit_batch without begin_batch raises error."""
        vm = temp_vault
        
        with pytest.raises(RuntimeError, match="No batch in progress"):
            vm.commit_batch()
    
    def test_rollback_without_batch_raises_error(self, temp_vault):
        """Calling rollback_batch without begin_batch raises error."""
        vm = temp_vault
        
        with pytest.raises(RuntimeError, match="No batch in progress"):
            vm.rollback_batch()


class TestJournalPersistFirst:
    """Test that journal capture writes remain persist-first (immediate commit)."""
    
    @pytest.fixture
    def temp_vault(self, tmp_path):
        vault_root = tmp_path / "vault"
        fake_home = tmp_path / "home"
        fake_home.mkdir(parents=True, exist_ok=True)
        
        import os
        os.environ['HOME'] = str(fake_home)
        os.environ['PERSONAL_OS_VAULT'] = str(vault_root)
        os.environ['GIT_AUTHOR_NAME'] = "Test"
        os.environ['GIT_AUTHOR_EMAIL'] = "test@test.local"
        os.environ['GIT_COMMITTER_NAME'] = "Test"
        os.environ['GIT_COMMITTER_EMAIL'] = "test@test.local"
        
        import personal_os.core.config as config
        import personal_os.core.vault as vault
        config._config = None
        vault._vault = None
        
        from personal_os.core.config import get_config
        cfg = get_config()
        
        return VaultManager(cfg.vault)
    
    def test_journal_entry_immediate_commit(self, temp_vault):
        """append_journal_entry commits immediately, not in batch."""
        vm = temp_vault
        
        # Capture initial commit count
        initial_commits = len(list(vm._repo.iter_commits()))
        
        # Append journal entry - should commit immediately
        vm.append_journal_entry("Test capture", capture_id="01TEST00000000000000001")
        
        # Should have exactly one new commit
        commits = list(vm._repo.iter_commits())
        assert len(commits) == initial_commits + 1
        assert "Journal entry" in commits[0].message
    
    def test_semantic_block_immediate_commit_outside_batch(self, temp_vault):
        """append_semantic_block commits immediately when not in batch."""
        vm = temp_vault
        
        # First create a journal entry
        vm.append_journal_entry("Test capture", capture_id="01TEST00000000000000002")
        
        initial_commits = len(list(vm._repo.iter_commits()))
        
        # Append semantic block - should commit immediately
        vm.append_semantic_block(
            "01TEST00000000000000002",
            [{"type": "task", "description": "Test task", "action": "CreateTask"}]
        )
        
        commits = list(vm._repo.iter_commits())
        assert len(commits) == initial_commits + 1
        assert "Semantic interpretation" in commits[0].message
    
    def test_journal_not_affected_by_batch(self, temp_vault):
        """Journal writes during a batch still commit immediately (safety)."""
        vm = temp_vault
        
        # Start a batch for processing writes
        vm.begin_batch("Processing batch")
        vm.write_file(vm.config.root / "processing.md", "Processing content")
        
        # Journal write should still commit immediately (persist-first)
        vm.append_journal_entry("Journal during batch", capture_id="01TEST00000000000000003")
        
        # Should have 2 commits: 1 for journal, 1 pending for batch
        commits = list(vm._repo.iter_commits())
        journal_commits = [c for c in commits if "Journal entry" in c.message]
        assert len(journal_commits) == 1
        
        # Now commit the batch
        vm.commit_batch()
        
        # Should have 2 commits total
        commits = list(vm._repo.iter_commits())
        assert len(commits) == 2  # 1 journal + 1 batch


class TestKnowledgeApplierBatchIntegration:
    """Test KnowledgeApplier uses batch for multiple actions."""
    
    @pytest.fixture
    def temp_vault(self, tmp_path):
        vault_root = tmp_path / "vault"
        fake_home = tmp_path / "home"
        fake_home.mkdir(parents=True, exist_ok=True)
        
        import os
        os.environ['HOME'] = str(fake_home)
        os.environ['PERSONAL_OS_VAULT'] = str(vault_root)
        os.environ['GIT_AUTHOR_NAME'] = "Test"
        os.environ['GIT_AUTHOR_EMAIL'] = "test@test.local"
        os.environ['GIT_COMMITTER_NAME'] = "Test"
        os.environ['GIT_COMMITTER_EMAIL'] = "test@test.local"
        
        import personal_os.core.config as config
        import personal_os.core.vault as vault
        config._config = None
        vault._vault = None
        
        from personal_os.core.config import get_config
        cfg = get_config()
        
        return VaultManager(cfg.vault)
    
    def test_multiple_actions_single_commit(self, temp_vault):
        """Applying multiple VaultActions in sequence uses batch."""
        applier = KnowledgeApplier(temp_vault)
        
        # Create a project first
        temp_vault.create_project("TestProject", "Test description")
        initial_commits = len(list(temp_vault._repo.iter_commits()))
        
        # Apply multiple actions - should use batch
        actions = [
            CreateTask(title="Task 1", project="TestProject", capture_id="01TEST00000000000000004"),
            RecordProgress(description="Progress 1", project="TestProject", capture_id="01TEST00000000000000004"),
            RecordActivity(description="Activity 1", project="TestProject", capture_id="01TEST00000000000000004"),
        ]
        
        # Manually batch since KnowledgeApplier doesn't auto-batch yet
        # (This tests the vault batch API works for multiple writes)
        temp_vault.begin_batch("Multiple actions")
        for action in actions:
            applier.apply_action(action)
        temp_vault.commit_batch()
        
        # Should have exactly one new commit for the batch
        commits = list(temp_vault._repo.iter_commits())
        assert len(commits) == initial_commits + 1
        assert "Multiple actions" in commits[0].message


class TestBackgroundProcessorBatchIntegration:
    """Test BackgroundProcessor uses batch for semantic processing."""
    
    @pytest.mark.asyncio
    async def test_semantic_processing_uses_batch(self, tmp_path):
        """_process_semantic uses batch for all processing-stage writes."""
        vault_root = tmp_path / "vault"
        fake_home = tmp_path / "home"
        fake_home.mkdir(parents=True, exist_ok=True)
        
        import os
        os.environ['HOME'] = str(fake_home)
        os.environ['PERSONAL_OS_VAULT'] = str(vault_root)
        os.environ['GIT_AUTHOR_NAME'] = "Test"
        os.environ['GIT_AUTHOR_EMAIL'] = "test@test.local"
        os.environ['GIT_COMMITTER_NAME'] = "Test"
        os.environ['GIT_COMMITTER_EMAIL'] = "test@test.local"
        os.environ['OPENROUTER_API_KEY'] = "test-key"  # Enable semantic path
        
        import personal_os.core.config as config
        import personal_os.core.vault as vault
        config._config = None
        vault._vault = None
        
        from personal_os.core.config import get_config
        cfg = get_config()
        vm = VaultManager(cfg.vault)
        
        # Create a project
        vm.create_project("TestProject", "Test")
        
        from personal_os.core.processor import BackgroundProcessor
        processor = BackgroundProcessor(vm)
        
        # Mock the semantic extractor to return predictable actions
        from personal_os.core.extraction import SemanticExtractor, ExtractionContext
        from personal_os.core.llm import LLMProvider, get_provider
        from personal_os.core.extraction.models import (
            RawExtraction, ExtractionType, RawProjectRef, RawDeadline, DeadlineKind
        )
        from unittest.mock import AsyncMock, MagicMock, patch
        from datetime import datetime, date
        
        # Mock provider
        mock_provider = MagicMock(spec=LLMProvider)
        raw_extractions = [
            RawExtraction(
                type=ExtractionType.TASK,
                description="Test task",
                project_ref=RawProjectRef(name="TestProject", confidence=0.9),
                deadline=RawDeadline(kind=DeadlineKind.RELATIVE_DAY, raw_text="tomorrow", confidence=0.9),
                confidence=0.9,
                certainty=0.8,
            ),
            RawExtraction(
                type=ExtractionType.PROGRESS,
                description="Test progress",
                project_ref=RawProjectRef(name="TestProject", confidence=0.9),
                confidence=0.9,
                certainty=0.9,
            ),
        ]
        mock_provider.extract = AsyncMock(return_value=raw_extractions)
        mock_provider.close = AsyncMock()
        
        initial_commits = len(list(vm._repo.iter_commits()))
        
        # Patch get_provider to return our mock
        with patch('personal_os.core.llm.get_provider', return_value=mock_provider):
            # Process an entry
            result = await processor.process(
                "I need to do the test task tomorrow. Made good progress today.",
                source="Text",
                capture_id="01TEST00000000000000005"
            )
        
        # Should have exactly one new commit for the batch (journal was already committed by persist_capture)
        # Note: persist_capture is called separately, not by BackgroundProcessor
        commits = list(vm._repo.iter_commits())
        batch_commits = [c for c in commits if "Semantic processing" in c.message]
        assert len(batch_commits) == 1, f"Expected 1 batch commit, got {len(batch_commits)}: {[c.message for c in commits]}"


class TestExistingSingleWriteCallers:
    """Test that existing code using single writes still works."""
    
    @pytest.fixture
    def temp_vault(self, tmp_path):
        vault_root = tmp_path / "vault"
        fake_home = tmp_path / "home"
        fake_home.mkdir(parents=True, exist_ok=True)
        
        import os
        os.environ['HOME'] = str(fake_home)
        os.environ['PERSONAL_OS_VAULT'] = str(vault_root)
        os.environ['GIT_AUTHOR_NAME'] = "Test"
        os.environ['GIT_AUTHOR_EMAIL'] = "test@test.local"
        os.environ['GIT_COMMITTER_NAME'] = "Test"
        os.environ['GIT_COMMITTER_EMAIL'] = "test@test.local"
        
        import personal_os.core.config as config
        import personal_os.core.vault as vault
        config._config = None
        vault._vault = None
        
        from personal_os.core.config import get_config
        cfg = get_config()
        
        return VaultManager(cfg.vault)
    
    def test_write_file_without_batch(self, temp_vault):
        """write_file without batch still commits immediately."""
        vm = temp_vault
        initial_commits = len(list(vm._repo.iter_commits()))
        
        vm.write_file(vm.config.root / "standalone.md", "Standalone content")
        
        commits = list(vm._repo.iter_commits())
        assert len(commits) == initial_commits + 1
        assert "Update standalone.md" in commits[0].message
    
    def test_append_file_without_batch(self, temp_vault):
        """append_file without batch still commits immediately."""
        vm = temp_vault
        vm.write_file(vm.config.root / "existing.md", "Initial\n")
        initial_commits = len(list(vm._repo.iter_commits()))
        
        vm.append_file(vm.config.root / "existing.md", "Appended\n")
        
        commits = list(vm._repo.iter_commits())
        assert len(commits) == initial_commits + 1
    
    def test_delete_file_without_batch(self, temp_vault):
        """delete_file without batch still commits immediately."""
        vm = temp_vault
        vm.write_file(vm.config.root / "to_delete.md", "Will delete")
        initial_commits = len(list(vm._repo.iter_commits()))
        
        vm.delete_file(vm.config.root / "to_delete.md")
        
        commits = list(vm._repo.iter_commits())
        assert len(commits) == initial_commits + 1
        assert "Delete to_delete.md" in commits[0].message
    
    def test_create_project_without_batch(self, temp_vault):
        """create_project still works (commits each file + final commit)."""
        vm = temp_vault
        
        project_dir = vm.create_project("NewProject", "Description")
        
        assert project_dir.exists()
        assert (project_dir / "README.md").exists()
        assert (project_dir / "Tasks.md").exists()
        
        # Should have commits for each file + final project commit
        commits = list(vm._repo.iter_commits())
        create_commits = [c for c in commits if "Create project" in c.message or "Update" in c.message]
        assert len(create_commits) >= 8  # README + 7 standard files + final commit


if __name__ == "__main__":
    pytest.main([__file__, "-v"])