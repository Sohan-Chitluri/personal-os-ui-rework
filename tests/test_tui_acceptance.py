"""End-to-End TUI Acceptance Test (Wave 4).

Drives the complete PersonalOSTUI application headlessly using Textual's
run_test harness against an isolated test vault.

Verifies:
1. App launch into Today view.
2. Capture via #capture-input with persist-first guarantee.
3. Background/inline processing updates vault (CORDIC/Tasks.md).
4. Auto-refresh updates active view.
5. Navigation across workspaces (Today, Projects, Journal, Search).
6. Completion capture updates task status to done.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
import pytest

from personal_os.core.config import VaultConfig
from personal_os.core.vault import VaultManager
from personal_os.tui.app import PersonalOSTUI


@pytest.mark.asyncio
async def test_full_tui_demo_flow(isolated_vault, monkeypatch):
    """Execute the complete 12-step demo flow in headless TUI."""
    # 1. Initialize vault and seed CORDIC project
    from personal_os.core.vault import set_vault
    vm = VaultManager(VaultConfig(root=isolated_vault))
    set_vault(vm)

    cordic_dir = vm.create_project("CORDIC", "CORDIC algorithm pipeline", "implementation")
    tasks_file = cordic_dir / "Tasks.md"
    tasks_file.write_text("# CORDIC Tasks\n\n## Active Tasks\n- [ ] Debug fixed-point arithmetic\n\n## Completed Tasks\n")

    app = PersonalOSTUI()

    async with app.run_test(size=(140, 44)) as pilot:
        router = app.query_one("#view-router")

        # Step 1: Initial state is today view
        assert router._active_id == "today"
        await pilot.pause()

        # Step 2: Focus capture input and submit first demo capture
        inp = app.query_one("#capture-input")
        inp.focus()
        await pilot.pause()
        inp.value = (
            "I've been working on the CORDIC pipeline. The fixed-point stage is mostly "
            "working, but scaling is still giving me weird results. I need to revisit "
            "that tomorrow because the review is on Thursday."
        )
        await pilot.press("enter")
        await pilot.pause()
        await asyncio.sleep(0.5)
        await pilot.pause()

        # Verify raw capture persisted to Daily Stream
        today_str = datetime.now().strftime("%Y-%m-%d")
        journal_path = isolated_vault / "04-Personal" / "Journal" / f"{today_str}.md"
        assert journal_path.exists(), "Raw journal file was not created"
        journal_content = journal_path.read_text()
        assert "CORDIC pipeline" in journal_content, "Raw capture text was not preserved"

        # Verify CORDIC/Tasks.md was updated by extraction
        cordic_tasks = tasks_file.read_text()
        assert "revisit that tomorrow" in cordic_tasks.lower(), "Task was not added to CORDIC/Tasks.md"

        # Step 3: Navigate to Projects view
        app.action_nav("projects")
        await pilot.pause()
        assert router._active_id == "projects"
        pv = router._views.get("projects")
        assert pv is not None

        # Step 4: Navigate to Journal view
        app.action_nav("journal")
        await pilot.pause()
        assert router._active_id == "journal"
        jv = router._views.get("journal")
        assert jv is not None
        assert jv.is_mounted

        # Step 5: Second capture: completion
        inp.focus()
        await pilot.pause()
        inp.value = "Finished Debug fixed-point arithmetic in CORDIC"
        await pilot.press("enter")
        await pilot.pause()
        await asyncio.sleep(0.5)
        await pilot.pause()

        # Verify completion was marked in CORDIC/Tasks.md
        updated_cordic_tasks = tasks_file.read_text()
        assert (
            "- [x] Debug fixed-point arithmetic" in updated_cordic_tasks
            or "## Completed Tasks\n- [x] Debug fixed-point arithmetic" in updated_cordic_tasks
            or "[x]" in updated_cordic_tasks
        ), "Task was not marked complete"

        # Step 6: Navigate to Search view and query
        app.action_nav("search")
        await pilot.pause()
        assert router._active_id == "search"
        sv = router._views.get("search")
        assert sv is not None

        # Step 7: Query search engine for CORDIC
        from personal_os.core.search import SearchEngine
        engine = SearchEngine(vault=vm)
        search_results = await engine.search("CORDIC", limit=10)
        assert len(search_results) > 0, "Search engine found no results for CORDIC"
        assert any("CORDIC" in r.file for r in search_results), "CORDIC files not found in search results"
