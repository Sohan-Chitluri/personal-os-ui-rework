"""Lightweight smoke tests for the Personal OS package.

Each test exercises a real code path against an ISOLATED temp vault (see
conftest.py). No heavy mocking: we call the actual services and assert on real
side effects (files created, results returned).

Run:  cd <repo> && PYTHONPATH=src python -m pytest tests/ -v -s
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# 1. CLI startup
# --------------------------------------------------------------------------- #
def test_cli_help_returns_usage():
    """`pos --help` (via module) must print usage and exit 0."""
    repo_root = Path(__file__).resolve().parent.parent
    env = {
        **__import__("os").environ,
        "PYTHONPATH": str(repo_root / "src"),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "personal_os.cli.main", "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo_root),
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"--help exited {proc.returncode}: {out}"
    assert "Usage" in out or "usage" in out, f"no usage text: {out}"


# --------------------------------------------------------------------------- #
# 2. Vault initialization
# --------------------------------------------------------------------------- #
def test_vault_init_creates_structure(isolated_vault):
    from personal_os.core.config import VaultConfig
    from personal_os.core.vault import VaultManager

    vm = VaultManager(VaultConfig(root=isolated_vault))
    expected = [
        "00-Inbox", "01-Academic", "02-Projects", "03-Skills",
        "04-Personal/Journal", "05-Resources", "06-Archive", "Templates",
    ]
    for d in expected:
        assert (isolated_vault / d).is_dir(), f"missing dir {d}"
    # git-initialized
    assert (isolated_vault / ".git").is_dir(), "vault not git-initialized"
    assert vm._repo is not None


# --------------------------------------------------------------------------- #
# 3. Project creation
# --------------------------------------------------------------------------- #
def test_create_project_eight_files(isolated_vault):
    from personal_os.core.config import VaultConfig
    from personal_os.core.vault import VaultManager

    vm = VaultManager(VaultConfig(root=isolated_vault))
    project_dir = vm.create_project("smoke-test", "a smoke test project")

    assert project_dir.is_dir()
    assert project_dir.parent == isolated_vault / "02-Projects"
    expected_files = [
        "README.md", "Progress.md", "Tasks.md", "Notes.md",
        "Architecture.md", "Decisions.md", "Research.md", "Timeline.md",
    ]
    for f in expected_files:
        assert (project_dir / f).is_file(), f"missing project file {f}"
    assert len(expected_files) == 8


# --------------------------------------------------------------------------- #
# 4. Note capture
# --------------------------------------------------------------------------- #
def test_note_capture_writes_journal(isolated_vault):
    from personal_os.core.config import VaultConfig
    from personal_os.core.vault import VaultManager

    vm = VaultManager(VaultConfig(root=isolated_vault))
    path = vm.append_journal_entry("Smoke test journal entry", source="Text")

    today = datetime.now().strftime("%Y-%m-%d")
    assert path.name == f"{today}.md"
    assert path.parent == isolated_vault / "04-Personal" / "Journal"
    assert path.is_file()
    assert "Smoke test journal entry" in path.read_text()


# --------------------------------------------------------------------------- #
# 5. Processing
# --------------------------------------------------------------------------- #
def test_process_entry_extracts(isolated_vault):
    from personal_os.core.config import VaultConfig
    from personal_os.core.vault import VaultManager
    from personal_os.core.processor import process_entry, ProcessingResult

    # ensure the singleton vault (used by process_entry) points at temp root
    VaultManager(VaultConfig(root=isolated_vault))

    result = asyncio.run(
        process_entry("Spent 2 hours on Maestro fixing the router bug", "Text")
    )
    assert isinstance(result, ProcessingResult)
    assert not result.errors, f"processing errors: {result.errors}"
    assert result.extractions or result.changes_made, "nothing extracted"


# --------------------------------------------------------------------------- #
# 6. Search
# --------------------------------------------------------------------------- #
def test_search_finds_keyword(isolated_vault):
    from personal_os.core.config import VaultConfig
    from personal_os.core.vault import VaultManager
    from personal_os.core.search import search, SearchResult

    vm = VaultManager(VaultConfig(root=isolated_vault))
    note = isolated_vault / "05-Resources" / "note.md"
    vm.write_file(note, "# Note\n\nThis contains the keyword zebrafish here.\n")

    results = asyncio.run(search("zebrafish", limit=10))
    assert isinstance(results, list)
    assert len(results) >= 1, "keyword not found by search"
    assert all(isinstance(r, SearchResult) for r in results)


# --------------------------------------------------------------------------- #
# 7. Links
# --------------------------------------------------------------------------- #
def test_links_related_and_orphans(isolated_vault):
    from personal_os.core.config import VaultConfig
    from personal_os.core.vault import VaultManager
    from personal_os.core import links

    vm = VaultManager(VaultConfig(root=isolated_vault))
    vm.write_file(
        isolated_vault / "05-Resources" / "a.md",
        "# A\n\nDiscusses the topic widgets in detail.\n",
    )
    vm.write_file(
        isolated_vault / "05-Resources" / "b.md",
        "# B\n\nMore on widgets and gadgets.\n",
    )

    related = links.find_related("widgets")
    assert isinstance(related, list)
    assert len(related) >= 1

    suggestions = links.suggest_links("widgets")
    assert isinstance(suggestions, list)

    orphans = links.find_orphans()
    assert isinstance(orphans, list)


# --------------------------------------------------------------------------- #
# 8. Weekly review
# --------------------------------------------------------------------------- #
def test_weekly_review_writes_file(isolated_vault):
    from personal_os.core.config import VaultConfig
    from personal_os.core.vault import VaultManager
    from personal_os.core import weekly_review

    VaultManager(VaultConfig(root=isolated_vault))

    out = weekly_review.generate_review()
    assert isinstance(out, Path)
    assert out.is_file()
    assert out.suffix == ".md"
    reviews_dir = isolated_vault / "04-Personal" / "Reviews" / "weekly"
    assert reviews_dir in out.parents
    assert "Weekly Review" in out.read_text()
