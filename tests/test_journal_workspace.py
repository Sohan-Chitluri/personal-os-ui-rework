"""Journal workspace focused tests.

Tests JournalView, JournalList, JournalDateNavigator components, and JournalProvider integration.
"""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from personal_os.core.config import VaultConfig
from personal_os.core.vault import VaultManager
from personal_os.tui.providers.journal_provider import (
    JournalProvider,
    JournalData,
    JournalDayData,
    JournalEntry,
    SemanticItem,
    create_journal_provider,
)
from personal_os.tui.components.journal_entry import (
    JournalList,
    JournalDateNavigator,
    JournalEntryWidget,
    JournalDateHeader,
    JournalEmptyState,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def isolated_vault():
    """Create a temporary vault with test journal data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_path = Path(tmpdir)
        config = VaultConfig(root=vault_path)
        vm = VaultManager(config)
        
        # Create test journal entries for today
        today = datetime.now()
        today_str = today.strftime("%Y-%m-%d")
        journal_path = vm.config.journal_dir / f"{today_str}.md"
        
        journal_content = f"# {today_str}\n\n"
        journal_content += "## 10:00:00 - [Source: Text] [cid: 01ABC123]\n\n"
        journal_content += "Working on the CORDIC scaling issue today.\n\n"
        journal_content += "## 11:30:00 - [Source: Voice] [cid: 01DEF456]\n\n"
        journal_content += "Fixed the scaling problem in the CORDIC algorithm.\n\n"
        
        # Add semantic block for first entry
        import yaml
        semantic_block = {
            "processed": (today + timedelta(seconds=30)).isoformat(timespec="seconds"),
            "items": [
                {
                    "type": "problem",
                    "description": "CORDIC scaling issue",
                    "project": "CORDIC",
                    "certainty": 0.8,
                    "confidence": 0.7,
                    "temporal_context": "today",
                    "action": "CreateTask"
                }
            ]
        }
        yaml_text = yaml.dump(semantic_block, default_flow_style=False, allow_unicode=True)
        journal_content += f"\n<!-- semantic:01ABC123\n{yaml_text}-->\n"
        
        journal_path.write_text(journal_content)
        
        # Create journal for yesterday
        yesterday = today - timedelta(days=1)
        yesterday_str = yesterday.strftime("%Y-%m-%d")
        yesterday_path = vm.config.journal_dir / f"{yesterday_str}.md"
        yesterday_content = f"# {yesterday_str}\n\n"
        yesterday_content += "## 14:00:00 - [Source: Text]\n\n"
        yesterday_content += "Yesterday's capture without semantic block.\n\n"
        yesterday_path.write_text(yesterday_content)
        
        # Create journal for 3 days ago
        three_days_ago = today - timedelta(days=3)
        three_days_str = three_days_ago.strftime("%Y-%m-%d")
        three_days_path = vm.config.journal_dir / f"{three_days_str}.md"
        three_days_content = f"# {three_days_str}\n\n"
        three_days_content += "## 09:00:00 - [Source: Text] [cid: 01GHI789]\n\n"
        three_days_content += "Old capture with semantic.\n\n"
        semantic_block2 = {
            "processed": (three_days_ago + timedelta(seconds=10)).isoformat(timespec="seconds"),
            "items": [
                {
                    "type": "task",
                    "description": "Review PR for Maestro",
                    "project": "Maestro",
                    "certainty": 0.9,
                    "confidence": 0.85,
                    "action": "CreateTask"
                }
            ]
        }
        yaml_text2 = yaml.dump(semantic_block2, default_flow_style=False, allow_unicode=True)
        three_days_content += f"\n<!-- semantic:01GHI789\n{yaml_text2}-->\n"
        three_days_path.write_text(three_days_content)
        
        yield vm


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
# JournalEntry Tests
# --------------------------------------------------------------------------- #

class TestJournalEntry:
    """Test JournalEntry dataclass."""
    
    def test_journal_entry_creation(self):
        """Test JournalEntry can be created."""
        entry = JournalEntry(
            time="10:00:00",
            source="Text",
            capture_id="01ABC123",
            content="Test content",
            raw="## 10:00:00 - [Source: Text] [cid: 01ABC123]\n\nTest content\n"
        )
        
        assert entry.time == "10:00:00"
        assert entry.source == "Text"
        assert entry.capture_id == "01ABC123"
        assert entry.content == "Test content"
    
    def test_journal_entry_with_semantic(self):
        """Test JournalEntry with semantic items."""
        semantic = SemanticItem(
            type="problem",
            description="CORDIC scaling issue",
            project="CORDIC",
            certainty=0.8,
            confidence=0.7
        )
        
        entry = JournalEntry(
            time="10:00:00",
            source="Text",
            capture_id="01ABC123",
            content="Test content",
            semantic_items=[semantic],
            has_semantic=True
        )
        
        assert entry.has_semantic is True
        assert len(entry.semantic_items) == 1
        assert entry.semantic_items[0].type == "problem"


# --------------------------------------------------------------------------- #
# JournalDayData Tests
# --------------------------------------------------------------------------- #

class TestJournalDayData:
    """Test JournalDayData dataclass."""
    
    def test_journal_day_data_creation(self):
        """Test JournalDayData can be created."""
        today = datetime.now()
        entry = JournalEntry(time="10:00:00", source="Text", content="Test")
        day = JournalDayData(date=today, entries=[entry])
        
        assert day.date == today
        assert len(day.entries) == 1
        assert day.entry_count == 1


# --------------------------------------------------------------------------- #
# JournalData Tests
# --------------------------------------------------------------------------- #

class TestJournalData:
    """Test JournalData dataclass."""
    
    def test_journal_data_creation(self):
        """Test JournalData can be created."""
        today = datetime.now()
        day = JournalDayData(date=today, entries=[])
        data = JournalData(days=[day], selected_date=today)
        
        assert len(data.days) == 1
        assert data.selected_date == today
    
    def test_get_day(self):
        """Test get_day finds day by date."""
        today = datetime.now()
        yesterday = today - timedelta(days=1)
        
        day1 = JournalDayData(date=today, entries=[])
        day2 = JournalDayData(date=yesterday, entries=[])
        
        data = JournalData(days=[day1, day2])
        
        result = data.get_day(today)
        assert result == day1
        
        result = data.get_day(yesterday)
        assert result == day2
        
        result = data.get_day(today + timedelta(days=1))
        assert result is None
    
    def test_expanded_entries(self):
        """Test expanded entries tracking."""
        data = JournalData()
        
        assert data.is_expanded("cid1") is False
        
        data.expanded_entries["cid1"] = True
        assert data.is_expanded("cid1") is True
        
        new_state = data.toggle_expanded("cid1")
        assert new_state is False
        assert data.is_expanded("cid1") is False


# --------------------------------------------------------------------------- #
# SemanticItem Tests
# --------------------------------------------------------------------------- #

class TestSemanticItem:
    """Test SemanticItem dataclass."""
    
    def test_semantic_item_creation(self):
        """Test SemanticItem can be created."""
        item = SemanticItem(
            type="task",
            description="Fix CORDIC scaling",
            project="CORDIC",
            certainty=0.8,
            confidence=0.7,
            temporal_context="tomorrow",
            action="CreateTask"
        )
        
        assert item.type == "task"
        assert item.description == "Fix CORDIC scaling"
        assert item.project == "CORDIC"
        assert item.certainty == 0.8
        assert item.confidence == 0.7
        assert item.temporal_context == "tomorrow"
        assert item.action == "CreateTask"


# --------------------------------------------------------------------------- #
# JournalProvider Tests
# --------------------------------------------------------------------------- #

class TestJournalProvider:
    """Test JournalProvider data layer."""
    
    def test_provider_initialization(self, app_services):
        """Test JournalProvider initializes correctly."""
        provider = create_journal_provider(app_services)
        
        assert isinstance(provider, JournalProvider)
        assert provider.vault is not None
        assert provider.config is not None
    
    def test_get_data(self, app_services, isolated_vault):
        """Test get_data returns journal data."""
        provider = create_journal_provider(app_services)
        data = provider.get_data()
        
        assert isinstance(data, JournalData)
        assert len(data.days) >= 1  # At least today
        assert data.selected_date is not None
    
    def test_journal_day_structure(self, app_services, isolated_vault):
        """Test journal day has expected structure."""
        provider = create_journal_provider(app_services)
        data = provider.get_data()
        
        # Should have at least today
        today = datetime.now()
        day = data.get_day(today)
        assert day is not None
        assert len(day.entries) >= 2  # Two entries in our test data
    
    def test_entry_has_semantic(self, app_services, isolated_vault):
        """Test entries with semantic blocks are detected."""
        provider = create_journal_provider(app_services)
        data = provider.get_data()
        
        today = datetime.now()
        day = data.get_day(today)
        
        # First entry should have semantic (cid: 01ABC123)
        entry_with_semantic = None
        for entry in day.entries:
            if entry.capture_id == "01ABC123":
                entry_with_semantic = entry
                break
        
        assert entry_with_semantic is not None
        assert entry_with_semantic.has_semantic is True
        assert len(entry_with_semantic.semantic_items) == 1
        assert entry_with_semantic.semantic_items[0].type == "problem"
        assert entry_with_semantic.semantic_items[0].project == "CORDIC"
        assert entry_with_semantic.semantic_items[0].certainty == 0.8
    
    def test_entry_without_semantic(self, app_services, isolated_vault):
        """Test entries without semantic blocks are handled."""
        provider = create_journal_provider(app_services)
        data = provider.get_data()
        
        today = datetime.now()
        day = data.get_day(today)
        
        # Second entry should not have semantic (no cid in our test)
        entry_without_semantic = None
        for entry in day.entries:
            if entry.capture_id == "01DEF456":
                entry_without_semantic = entry
                break
        
        assert entry_without_semantic is not None
        assert entry_without_semantic.has_semantic is False
    
    def test_select_date(self, app_services, isolated_vault):
        """Test select_date updates selected date."""
        provider = create_journal_provider(app_services)
        data = provider.get_data()
        
        yesterday = datetime.now() - timedelta(days=1)
        result = provider.select_date(yesterday)
        
        assert result is True
        data = provider.get_data()
        assert data.selected_date.date() == yesterday.date()
    
    def test_toggle_semantic(self, app_services, isolated_vault):
        """Test toggle_semantic toggles expanded state."""
        provider = create_journal_provider(app_services)
        data = provider.get_data()
        
        today = datetime.now()
        day = data.get_day(today)
        
        entry_with_semantic = None
        for entry in day.entries:
            if entry.capture_id == "01ABC123":
                entry_with_semantic = entry
                break
        
        assert entry_with_semantic is not None
        
        # Toggle to expanded
        result = provider.toggle_semantic(entry_with_semantic.capture_id)
        assert result is True
        assert provider.is_expanded(entry_with_semantic.capture_id) is True
        
        # Toggle back
        result = provider.toggle_semantic(entry_with_semantic.capture_id)
        assert result is False
        assert provider.is_expanded(entry_with_semantic.capture_id) is False
    
    def test_refresh(self, app_services, isolated_vault):
        """Test refresh invalidates cache and returns new data."""
        provider = create_journal_provider(app_services)
        data1 = provider.get_data()
        
        # Add a new journal entry
        today = datetime.now()
        today_str = today.strftime("%Y-%m-%d")
        journal_path = isolated_vault.config.journal_dir / f"{today_str}.md"
        
        # Append new entry
        new_entry = "\n## 12:00:00 - [Source: Text] [cid: 01NEW999]\n\nNew capture.\n"
        journal_path.write_text(journal_path.read_text() + new_entry)
        
        data2 = provider.refresh()
        
        # Should still have same number of days but more entries today
        assert len(data2.days) == len(data1.days)
        today_day = data2.get_day(today)
        assert len(today_day.entries) == len(data1.get_day(today).entries) + 1


# --------------------------------------------------------------------------- #
# JournalEntryWidget Component Tests
# --------------------------------------------------------------------------- #

class TestJournalEntryWidget:
    """Test JournalEntryWidget UI component."""
    
    def test_journal_entry_widget_creation(self, isolated_vault):
        """Test JournalEntryWidget can be created."""
        entry = JournalEntry(
            time="10:00:00",
            source="Text",
            capture_id="01ABC123",
            content="Test content"
        )
        
        widget = JournalEntryWidget(entry, is_expanded=False, on_toggle=lambda x: None)
        
        assert widget.entry == entry
        assert widget.is_expanded is False
    
    def test_journal_entry_widget_expanded(self, isolated_vault):
        """Test JournalEntryWidget with expanded state."""
        semantic = SemanticItem(
            type="problem",
            description="Test issue",
            project="Test",
            certainty=0.5
        )
        entry = JournalEntry(
            time="10:00:00",
            source="Text",
            capture_id="01ABC123",
            content="Test content",
            semantic_items=[semantic],
            has_semantic=True
        )
        
        widget = JournalEntryWidget(entry, is_expanded=True, on_toggle=lambda x: None)
        
        assert widget.is_expanded is True
        assert widget.entry.has_semantic is True
    
    def test_journal_entry_widget_voice_source(self, isolated_vault):
        """Test JournalEntryWidget with voice source."""
        entry = JournalEntry(
            time="10:00:00",
            source="Voice",
            capture_id="01ABC123",
            content="Voice capture"
        )
        
        widget = JournalEntryWidget(entry, is_expanded=False, on_toggle=lambda x: None)
        
        assert widget.entry.source == "Voice"


# --------------------------------------------------------------------------- #
# JournalDateHeader Tests
# --------------------------------------------------------------------------- #

class TestJournalDateHeader:
    """Test JournalDateHeader component."""
    
    def test_journal_date_header_today(self):
        """Test JournalDateHeader for today."""
        today = datetime.now()
        header = JournalDateHeader(today, 2, is_today=True)
        
        rendered = header.render()
        assert "Today" in rendered
        assert "2 entries" in rendered
    
    def test_journal_date_header_yesterday(self):
        """Test JournalDateHeader for yesterday."""
        yesterday = datetime.now() - timedelta(days=1)
        header = JournalDateHeader(yesterday, 1, is_today=False)
        
        rendered = header.render()
        assert "Yesterday" in rendered
        assert "1 entry" in rendered
    
    def test_journal_date_header_other(self):
        """Test JournalDateHeader for other days."""
        other_day = datetime.now() - timedelta(days=5)
        header = JournalDateHeader(other_day, 3, is_today=False)
        
        rendered = header.render()
        assert "Yesterday" not in rendered
        assert "Today" not in rendered
        assert "3 entries" in rendered


# --------------------------------------------------------------------------- #
# JournalEmptyState Tests
# --------------------------------------------------------------------------- #

class TestJournalEmptyState:
    """Test JournalEmptyState component."""
    
    def test_journal_empty_state_render(self):
        """Test JournalEmptyState renders correctly."""
        empty = JournalEmptyState()
        rendered = empty.render()
        
        assert "No journal entries" in rendered
        assert "Capture something" in rendered


# --------------------------------------------------------------------------- #
# JournalList Component Tests
# --------------------------------------------------------------------------- #

class TestJournalListComponent:
    """Test JournalList UI component."""
    
    def test_journal_list_creation(self, isolated_vault):
        """Test JournalList initializes correctly."""
        list_widget = JournalList()
        
        assert list_widget._data is None
    
    def test_journal_list_update_data(self, app_services, isolated_vault):
        """Test update_data refreshes list with new data."""
        provider = create_journal_provider(app_services)
        data = provider.get_data()
        
        list_widget = JournalList()
        list_widget.update_data(data)
        
        assert list_widget._data == data
    
    def test_journal_list_empty_state(self):
        """Test JournalList shows empty state when no data."""
        list_widget = JournalList()
        list_widget._data = JournalData()
        
        composed = list(list_widget.compose())
        # Should have empty state
        assert any("No journal entries" in str(c.render()) for c in composed)
    
    def test_handle_toggle(self, app_services, isolated_vault):
        """Test _handle_toggle toggles semantic expansion."""
        provider = create_journal_provider(app_services)
        data = provider.get_data()
        
        today = datetime.now()
        day = data.get_day(today)
        
        entry_with_semantic = None
        for entry in day.entries:
            if entry.capture_id == "01ABC123":
                entry_with_semantic = entry
                break
        
        assert entry_with_semantic is not None
        
        list_widget = JournalList()
        list_widget.update_data(data)
        
        # Toggle expansion
        list_widget._handle_toggle(entry_with_semantic.capture_id)
        
        # Check data was updated
        assert data.is_expanded(entry_with_semantic.capture_id) is True


# --------------------------------------------------------------------------- #
# JournalDateNavigator Component Tests
# --------------------------------------------------------------------------- #

class TestJournalDateNavigatorComponent:
    """Test JournalDateNavigator UI component."""
    
    def test_journal_date_navigator_creation(self, isolated_vault):
        """Test JournalDateNavigator initializes correctly."""
        nav = JournalDateNavigator()
        
        assert nav._dates == []
        assert nav._entry_counts == {}
        assert nav._selected_date is None
    
    def test_journal_date_navigator_update_data(self, app_services, isolated_vault):
        """Test update_data refreshes navigator with new data."""
        provider = create_journal_provider(app_services)
        data = provider.get_data()
        
        nav = JournalDateNavigator()
        nav.update_data(data)
        
        assert len(nav._dates) == len(data.days)
        assert nav._entry_counts == {day.date: day.entry_count for day in data.days}
        assert nav._selected_date == data.selected_date
    
    def test_journal_date_navigator_empty_state(self):
        """Test JournalDateNavigator shows empty state when no data."""
        nav = JournalDateNavigator()
        nav._data = JournalData()
        
        composed = list(nav.compose())
        assert any("No journal entries" in str(c.render()) for c in composed)
    
    def test_select_date(self, app_services, isolated_vault):
        """Test select_date programmatically selects a date."""
        provider = create_journal_provider(app_services)
        data = provider.get_data()
        
        nav = JournalDateNavigator()
        nav.update_data(data)
        
        yesterday = datetime.now() - timedelta(days=1)
        nav.select_date(yesterday)
        
        assert nav._selected_date.date() == yesterday.date()


# --------------------------------------------------------------------------- #
# JournalView Integration Tests
# --------------------------------------------------------------------------- #

class TestJournalViewIntegration:
    """Test JournalView integration with provider and components."""
    
    def test_view_initialization(self, app_services):
        """Test JournalView initializes correctly."""
        from personal_os.tui.views.journal import JournalView
        
        view = JournalView(app_services)
        
        assert view.view_id == "journal"
        assert view.title == "Journal"
        assert view._provider is not None
    
    def test_view_on_activate(self, app_services):
        """Test JournalView on_activate loads data."""
        from personal_os.tui.views.journal import JournalView
        
        view = JournalView(app_services)
        view.on_activate()
        
        assert view._data is not None
        assert isinstance(view._data, JournalData)
    
    def test_view_refresh_content(self, app_services):
        """Test refresh_content reloads data."""
        from personal_os.tui.views.journal import JournalView
        
        view = JournalView(app_services)
        view.on_activate()
        
        import asyncio
        asyncio.run(view.refresh_content())
        
        assert view._data is not None
    
    def test_handle_date_select(self, app_services, isolated_vault):
        """Test _handle_date_select updates selected date."""
        from personal_os.tui.views.journal import JournalView
        
        view = JournalView(app_services)
        view.on_activate()
        
        yesterday = datetime.now() - timedelta(days=1)
        view._handle_date_select(yesterday)
        
        assert view._data.selected_date.date() == yesterday.date()
    
    def test_handle_toggle_semantic(self, app_services, isolated_vault):
        """Test _handle_toggle_semantic toggles semantic block."""
        from personal_os.tui.views.journal import JournalView
        
        view = JournalView(app_services)
        view.on_activate()
        
        today = datetime.now()
        day = view._data.get_day(today)
        
        entry_with_semantic = None
        for entry in day.entries:
            if entry.capture_id == "01ABC123":
                entry_with_semantic = entry
                break
        
        assert entry_with_semantic is not None
        
        view._handle_toggle_semantic(entry_with_semantic.capture_id)
        
        # Check data was updated
        assert view._data.is_expanded(entry_with_semantic.capture_id) is True


# --------------------------------------------------------------------------- #
# Handle Actions Tests
# --------------------------------------------------------------------------- #

class TestJournalActions:
    """Test Journal action handlers (stubs)."""
    
    def test_handle_reprocess(self, app_services, isolated_vault):
        """Test _handle_reprocess does not raise."""
        from personal_os.tui.views.journal import JournalView
        from textual.app import App
        
        class TestApp(App):
            pass
        
        app = TestApp()
        
        import asyncio
        
        async def test():
            async with app.run_test() as pilot:
                view = JournalView(app_services)
                view.on_activate()
                
                # Should not raise
                view._handle_reprocess("01ABC123")
                assert True  # Just verify no exception
        
        asyncio.run(test())
    
    def test_handle_link_project(self, app_services, isolated_vault):
        """Test _handle_link_project does not raise."""
        from personal_os.tui.views.journal import JournalView
        from textual.app import App
        
        class TestApp(App):
            pass
        
        app = TestApp()
        
        import asyncio
        
        async def test():
            async with app.run_test() as pilot:
                view = JournalView(app_services)
                view.on_activate()
                
                # Should not raise
                view._handle_link_project("01ABC123")
                assert True  # Just verify no exception
        
        asyncio.run(test())
    
    def test_handle_convert_task(self, app_services, isolated_vault):
        """Test _handle_convert_task does not raise."""
        from personal_os.tui.views.journal import JournalView
        from textual.app import App
        
        class TestApp(App):
            pass
        
        app = TestApp()
        
        import asyncio
        
        async def test():
            async with app.run_test() as pilot:
                view = JournalView(app_services)
                view.on_activate()
                
                # Should not raise
                view._handle_convert_task("01ABC123")
                assert True  # Just verify no exception
        
        asyncio.run(test())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])