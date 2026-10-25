"""Regression tests for Agent A: Vault Data Integrity (Findings #1 and #2)."""
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from personal_os.core.vault import VaultManager, VaultConfig


@pytest.fixture
def temp_vault(tmp_path):
    """Create an isolated temporary vault for testing."""
    vault_root = tmp_path / "vault"
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)

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

    vm = VaultManager(VaultConfig(root=vault_root))
    return vm


def test_rollback_batch_preserves_pre_existing_file(temp_vault):
    """Finding #1: Modifying an existing file in batch mode must restore original content on rollback."""
    vm = temp_vault
    target_file = vm.config.root / "02-Projects" / "TestProject" / "Tasks.md"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("# Original Tasks\n- [ ] Keep this task\n", encoding="utf-8")

    # Start batch and modify existing file
    vm.begin_batch("Failing batch")
    vm.write_file(target_file, "# Modified Tasks\n- [ ] Unwanted task\n")
    # Verify content was updated before rollback
    assert "Unwanted task" in target_file.read_text(encoding="utf-8")

    # Rollback batch
    vm.rollback_batch()

    # File MUST still exist and MUST contain original content
    assert target_file.exists(), "Pre-existing file was erroneously deleted on rollback!"
    content = target_file.read_text(encoding="utf-8")
    assert "# Original Tasks" in content
    assert "- [ ] Keep this task" in content
    assert "Unwanted task" not in content


def test_rollback_batch_removes_new_files_and_preserves_existing(temp_vault):
    """Finding #1: Rollback removes newly created files while restoring existing files."""
    vm = temp_vault
    existing_file = vm.config.root / "existing.md"
    existing_file.write_text("Original content", encoding="utf-8")
    new_file = vm.config.root / "brand_new.md"

    vm.begin_batch("Mixed batch")
    vm.write_file(existing_file, "Modified content")
    vm.write_file(new_file, "Brand new content")

    assert existing_file.exists()
    assert new_file.exists()

    vm.rollback_batch()

    # Existing file preserved with original content
    assert existing_file.exists()
    assert existing_file.read_text(encoding="utf-8") == "Original content"

    # Newly created file removed
    assert not new_file.exists()


def test_delete_file_commit_false_does_not_commit(temp_vault):
    """Finding #2: delete_file with commit=False must not create a git commit."""
    vm = temp_vault
    file_to_delete = vm.config.root / "delete_me.md"
    vm.write_file(file_to_delete, "Delete this", commit=True, message="Add file")

    initial_commit_count = len(list(vm._repo.iter_commits()))

    # Delete with commit=False
    vm.delete_file(file_to_delete, commit=False)

    assert not file_to_delete.exists()
    # Commit count must not increase
    assert len(list(vm._repo.iter_commits())) == initial_commit_count


def test_delete_file_without_git_repo(tmp_path):
    """Finding #2: delete_file must not crash if git is disabled or _repo is None."""
    vault_root = tmp_path / "no_git_vault"
    vault_root.mkdir(parents=True, exist_ok=True)

    vm = VaultManager(VaultConfig(root=vault_root))
    # Explicitly simulate a VaultManager without an active git repo
    vm._repo = None

    test_file = vault_root / "test.md"
    test_file.write_text("Non-git content", encoding="utf-8")

    # Should delete file cleanly without AttributeError: 'NoneType' object has no attribute 'index'
    vm.delete_file(test_file, commit=True)
    assert not test_file.exists()


def test_list_projects_nonexistent_directory(temp_vault):
    """list_projects safely returns [] if 02-Projects directory does not exist."""
    vm = temp_vault
    if vm.config.projects_dir.exists():
        import shutil
        shutil.rmtree(vm.config.projects_dir)
    assert not vm.config.projects_dir.exists()
    assert vm.list_projects() == []


def test_list_projects_empty_directory(temp_vault):
    """list_projects safely returns [] if 02-Projects directory exists but has no projects."""
    vm = temp_vault
    vm.config.projects_dir.mkdir(parents=True, exist_ok=True)
    assert vm.list_projects() == []


def test_list_projects_populated_directory(temp_vault):
    """list_projects returns metadata for populated projects."""
    vm = temp_vault
    vm.create_project("ProjectAlpha", "First project")
    vm.create_project("ProjectBeta", "Second project")

    projects = vm.list_projects()
    assert len(projects) == 2
    names = [p["name"] for p in projects]
    assert "ProjectAlpha" in names
    assert "ProjectBeta" in names


def test_atomic_write_normal_and_replacement(temp_vault):
    """atomic_write cleanly creates a new file and atomically replaces an existing file."""
    vm = temp_vault
    target_path = vm.config.root / "atomic_test.md"

    # Initial write
    with vm.atomic_write(target_path) as tmp:
        tmp.write_text("First version", encoding="utf-8")

    assert target_path.exists()
    assert target_path.read_text(encoding="utf-8") == "First version"

    # Replacement write
    with vm.atomic_write(target_path) as tmp:
        tmp.write_text("Second version", encoding="utf-8")

    assert target_path.read_text(encoding="utf-8") == "Second version"

    # Verify no temp files leaked
    tmp_files = list(vm.config.root.glob(".*.tmp"))
    assert tmp_files == []


def test_atomic_write_exception_cleans_up_temp_and_preserves_original(temp_vault):
    """When an exception occurs during atomic_write, the temp file is unlinked and original file is preserved."""
    vm = temp_vault
    target_path = vm.config.root / "resilient.md"
    target_path.write_text("Unchanged baseline", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Crash during write"), vm.atomic_write(target_path) as tmp:
        tmp.write_text("Corrupted write", encoding="utf-8")
        raise RuntimeError("Crash during write")

    # Original content preserved
    assert target_path.read_text(encoding="utf-8") == "Unchanged baseline"

    # Temp file was cleaned up
    tmp_files = list(vm.config.root.glob(".*.tmp"))
    assert tmp_files == []
