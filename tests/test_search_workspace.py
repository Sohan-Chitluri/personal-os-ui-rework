"""Search workspace focused tests.

Tests SearchView, SearchResults component, and SearchProvider integration.
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from personal_os.core.config import VaultConfig
from personal_os.core.vault import VaultManager
from personal_os.core.search import SearchEngine
from personal_os.tui.providers.search_provider import (
    SearchProvider,
    SearchResultsData,
    SearchResultItem,
    create_search_provider,
)
from personal_os.tui.components.search_results import SearchResults, SearchInput, SearchResultRow


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def isolated_vault():
    """Create a temporary vault with test data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_path = Path(tmpdir)
        config = VaultConfig(root=vault_path)
        vm = VaultManager(config)
        
        # Create test files
        # Project files
        project_dir = vm.config.projects_dir / "test-project"
        project_dir.mkdir(parents=True)
        vm.write_file(project_dir / "README.md", "# Test Project\n\nA test project for search.")
        vm.write_file(project_dir / "Tasks.md", "# Tasks\n\n## Active Tasks\n- [ ] Fix the bug\n- [ ] Write tests")
        vm.write_file(project_dir / "Progress.md", "# Progress\n\n**2026-01-15**: Fixed bug (2h)")
        
        # Journal
        journal_path = vm.config.journal_dir / "2026-01-15.md"
        journal_path.write_text("# 2026-01-15\n\n## 10:00:00 - [Source: Text]\n\nWorking on the CORDIC scaling issue.\n")
        
        # Resource
        resource_path = vm.config.root / "05-Resources" / "note.md"
        resource_path.parent.mkdir(parents=True, exist_ok=True)
        resource_path.write_text("# Note\n\nThis contains the keyword CORDIC for testing.\n")
        
        yield vm


@pytest.fixture
def search_engine(isolated_vault):
    """Create a SearchEngine with the isolated vault."""
    return SearchEngine(isolated_vault)


@pytest.fixture
def app_services(isolated_vault):
    """Mock AppServices for provider creation."""
    from dataclasses import dataclass
    
    @dataclass
    class MockAppServices:
        vault: VaultManager
        config: any = None
    
    return MockAppServices(vault=isolated_vault)


# --------------------------------------------------------------------------- #
# SearchEngine Tests
# --------------------------------------------------------------------------- #

class TestSearchEngine:
    """Test the core SearchEngine functionality."""
    
    @pytest.mark.asyncio
    async def test_basic_search(self, search_engine):
        """Test basic text search across vault."""
        results = await search_engine.search("CORDIC", limit=10)
        assert len(results) >= 2  # Should find in journal and resource
        assert all("CORDIC" in r.content or "CORDIC" in r.context for r in results)
    
    @pytest.mark.asyncio
    async def test_project_filter(self, search_engine):
        """Test project: filter."""
        results = await search_engine.search("project:test-project", limit=10)
        assert len(results) >= 1
        assert all("test-project" in r.file for r in results)
    
    @pytest.mark.asyncio
    async def test_type_filter(self, search_engine):
        """Test type: filter."""
        results = await search_engine.search("type:task bug", limit=10)
        assert len(results) >= 1
        assert all("Tasks.md" in r.file for r in results)
    
    @pytest.mark.asyncio
    async def test_tag_filter(self, search_engine):
        """Test tag: filter."""
        # Write a file with a tag
        vault = search_engine.vault
        vault.write_file(vault.config.projects_dir / "test-project" / "Notes.md", "# Notes\n\n#bug important note")
        
        results = await search_engine.search("tag:bug", limit=10)
        assert len(results) >= 1
        assert all("#bug" in r.content.lower() for r in results)
    
    @pytest.mark.asyncio
    async def test_exclude_filter(self, search_engine):
        """Test -exclude filter."""
        results = await search_engine.search("CORDIC -scaling", limit=10)
        # Should find CORDIC but exclude results with "scaling"
        for r in results:
            assert "scaling" not in r.content.lower()
    
    @pytest.mark.asyncio
    async def test_date_filter(self, search_engine):
        """Test date: filter."""
        results = await search_engine.search("date:today", limit=10)
        # Should not error
        assert isinstance(results, list)
    
    @pytest.mark.asyncio
    async def test_phrase_search(self, search_engine):
        """Test quoted phrase search."""
        results = await search_engine.search('"CORDIC scaling"', limit=10)
        assert isinstance(results, list)
    
    @pytest.mark.asyncio
    async def test_search_files(self, search_engine):
        """Test file pattern search."""
        files = await search_engine.search_files("*Tasks.md", limit=10)
        assert len(files) >= 1
        assert all(f.name == "Tasks.md" for f in files)


# --------------------------------------------------------------------------- #
# SearchProvider Tests
# --------------------------------------------------------------------------- #

class TestSearchProvider:
    """Test the SearchProvider data layer."""
    
    @pytest.mark.asyncio
    async def test_provider_search(self, app_services):
        """Test SearchProvider search method."""
        provider = create_search_provider(app_services)
        data = await provider.search("test", limit=50)
        
        assert isinstance(data, SearchResultsData)
        assert data.query == "test"
        assert not data.is_searching
        assert data.error is None
    
    @pytest.mark.asyncio
    async def test_provider_clear(self, app_services):
        """Test SearchProvider clear."""
        provider = create_search_provider(app_services)
        data = provider.clear()
        
        assert isinstance(data, SearchResultsData)
        assert data.query == ""
        assert data.results == []
    
    @pytest.mark.asyncio
    async def test_result_item_from_core(self, app_services, isolated_vault):
        """Test SearchResultItem.from_core_result."""
        from personal_os.core.search import SearchResult as CoreSearchResult
        
        core = CoreSearchResult(
            file="02-Projects/test-project/Tasks.md",
            line=5,
            content="- [ ] Fix bug",
            context="## Active Tasks\n- [ ] Fix bug\n- [ ] Write tests",
            match_type="text"
        )
        
        item = SearchResultItem.from_core_result(core, isolated_vault)
        
        assert item.file == "02-Projects/test-project/Tasks.md"
        assert item.line == 5
        assert item.project == "test-project"
        assert item.file_type == "task"
    
    @pytest.mark.asyncio
    async def test_group_by_file(self, app_services, isolated_vault):
        """Test SearchResultsData.group_by_file."""
        provider = create_search_provider(app_services)
        
        # Create items from same and different files
        from personal_os.core.search import SearchResult as CoreSearchResult
        
        core1 = CoreSearchResult(file="a.md", line=1, content="test", match_type="text")
        core2 = CoreSearchResult(file="a.md", line=2, content="test again", match_type="text")
        core3 = CoreSearchResult(file="b.md", line=1, content="test", match_type="text")
        
        items = [
            SearchResultItem.from_core_result(core1, isolated_vault),
            SearchResultItem.from_core_result(core2, isolated_vault),
            SearchResultItem.from_core_result(core3, isolated_vault),
        ]
        
        data = SearchResultsData(query="test", results=items)
        grouped = data.group_by_file()
        
        assert len(grouped) == 2
        assert len(grouped["a.md"]) == 2
        assert len(grouped["b.md"]) == 1


# --------------------------------------------------------------------------- #
# SearchResults Component Tests
# --------------------------------------------------------------------------- #

class TestSearchResultsComponent:
    """Test the SearchResults UI component."""
    
    def test_search_input_compose(self):
        """Test SearchInput renders correctly."""
        input_widget = SearchInput()
        
        # Check it has the right structure
        assert hasattr(input_widget, 'on_change')
        assert hasattr(input_widget, 'on_submit')
    
    def test_search_result_row_render(self, isolated_vault):
        """Test SearchResultRow renders correctly."""
        item = SearchResultItem(
            file="02-Projects/test/Tasks.md",
            line=10,
            content="test content",
            context="context here",
            project="test",
            file_type="task"
        )
        
        row = SearchResultRow(item, callback=lambda x: None)
        
        assert row.item == item
        assert not row.is_selected
    
    def test_search_results_empty_state(self):
        """Test SearchResults empty state."""
        from personal_os.tui.providers.search_provider import SearchResultsData
        results = SearchResults()
        results._data = SearchResultsData()
        
        # Should show empty state message
        composed = list(results.compose())
        # The empty state is rendered as a Static widget with the message
        assert any("No results" in str(c.render()) for c in composed)
    
    def test_search_results_searching_state(self):
        """Test SearchResults searching state."""
        from personal_os.tui.providers.search_provider import SearchResultsData
        results = SearchResults()
        results._data = SearchResultsData(query="test", is_searching=True)
        
        composed = list(results.compose())
        assert any("Searching" in str(c.render()) for c in composed)
    
    def test_search_results_error_state(self):
        """Test SearchResults error state."""
        from personal_os.tui.providers.search_provider import SearchResultsData
        results = SearchResults()
        results._data = SearchResultsData(query="test", error="Connection failed")
        
        composed = list(results.compose())
        assert any("Error" in str(c.render()) for c in composed)
    
    def test_search_results_with_data(self, isolated_vault):
        """Test SearchResults with actual results."""
        from personal_os.core.search import SearchResult as CoreSearchResult
        
        core = CoreSearchResult(
            file="02-Projects/test/Tasks.md",
            line=5,
            content="- [ ] Fix bug",
            context="## Active Tasks\n- [ ] Fix bug",
            match_type="text"
        )
        
        item = SearchResultItem.from_core_result(core, isolated_vault)
        data = SearchResultsData(query="bug", results=[item])
        
        results = SearchResults()
        results._data = data
        
        # Test data grouping without composing
        grouped = data.group_by_file()
        assert "02-Projects/test/Tasks.md" in grouped
        assert len(grouped["02-Projects/test/Tasks.md"]) == 1
        assert grouped["02-Projects/test/Tasks.md"][0].content == "- [ ] Fix bug"
        assert grouped["02-Projects/test/Tasks.md"][0].project == "test"
        assert grouped["02-Projects/test/Tasks.md"][0].file_type == "task"


# --------------------------------------------------------------------------- #
# SearchView Integration Tests
# --------------------------------------------------------------------------- #

class TestSearchViewIntegration:
    """Test SearchView integration with provider and components."""
    
    @pytest.mark.asyncio
    async def test_view_initialization(self, app_services):
        """Test SearchView initializes correctly."""
        from personal_os.tui.views.search import SearchView
        from personal_os.tui.services import AppServices
        
        view = SearchView(app_services)
        
        assert view.view_id == "search"
        assert view.title == "Search"
        assert view._provider is not None
    
    @pytest.mark.asyncio
    async def test_view_on_activate(self, app_services):
        """Test SearchView on_activate loads data."""
        from personal_os.tui.views.search import SearchView
        
        view = SearchView(app_services)
        view.on_activate()
        
        assert view._data is not None
        assert isinstance(view._data, SearchResultsData)
    
    @pytest.mark.asyncio
    async def test_view_perform_search(self, app_services):
        """Test SearchView _perform_search."""
        from personal_os.tui.views.search import SearchView
        
        view = SearchView(app_services)
        view.on_activate()
        
        await view._perform_search("test query")
        
        # Wait for debounce
        await asyncio.sleep(0.2)
        
        assert view._data.query == "test query"
        assert not view._data.is_searching


# --------------------------------------------------------------------------- #
# Keyboard Navigation Tests
# --------------------------------------------------------------------------- #

class TestSearchKeyboardNavigation:
    """Test keyboard bindings in SearchResults."""
    
    def test_bindings_defined(self):
        """Test that keyboard bindings are defined."""
        bindings = SearchResults.BINDINGS
        
        binding_keys = {b.key for b in bindings}
        assert "enter" in binding_keys
        assert "p" in binding_keys
        assert "f" in binding_keys
        assert "escape" in binding_keys
    
    def test_action_clear_search(self):
        """Test Escape clears search."""
        results = SearchResults()
        # This would need a mounted app to fully test
        # Just verify the method exists
        assert hasattr(results, 'action_clear_search')


# --------------------------------------------------------------------------- #
# Filter Chips Tests
# --------------------------------------------------------------------------- #

class TestSearchFilterChips:
    """Test filter chip parsing and rendering."""
    
    def test_parse_filters_from_query(self):
        """Test _parse_filters_from_query extracts filters."""
        results = SearchResults()
        
        query = "project:myproject type:task tag:bug date:today -exclude"
        filters = results._parse_filters_from_query(query)
        
        assert filters["project"] == "myproject"
        assert filters["type"] == "task"
        assert filters["tag"] == "bug"
        assert filters["date"] == "today"
        assert "exclude" in filters
    
    def test_render_filter_chips(self):
        """Test _render_filter_chips creates horizontal container."""
        from textual.containers import Horizontal
        from textual.app import App
        
        class TestApp(App):
            pass
        
        app = TestApp()
        
        # Need to run in app context for compose to work properly
        async def test():
            async with app.run_test() as pilot:
                results = SearchResults()
                filters = {"project": "test", "type": "task"}
                chips = results._render_filter_chips(filters)
                
                assert isinstance(chips, Horizontal)
                # Mount the widget to populate children
                await pilot.app.mount(chips)
                assert len(chips.children) == 2
        
        import asyncio
        asyncio.run(test())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])