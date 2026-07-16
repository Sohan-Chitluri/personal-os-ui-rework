"""Main TUI application for Personal OS."""
from __future__ import annotations
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.widgets import (
    Header, Footer, Input, RichLog, Static, Tree, Button,
    TabbedContent, TabPane, Label, ListView, ListItem,
    MarkdownViewer, TextArea, LoadingIndicator
)
from textual.binding import Binding
from textual.message import Message
from textual.events import Key
from textual.screen import Screen
from textual.reactive import reactive
from textual.worker import Worker, get_current_worker

from personal_os.core.config import get_config, Config, VaultConfig
from personal_os.core.vault import get_vault, VaultManager
from personal_os.core.processor import process_entry, BackgroundProcessor, ProcessingResult
from personal_os.core.search import search, SearchResult, SearchEngine
from personal_os.tui.widgets.panels import (
    ProjectsPanel, TasksPanel, JournalPanel,
    SearchPanel, CalendarPanel, SettingsPanel
)


# ========== Messages ==========

class ChatMessage(Message):
    """A message in the conversation."""
    def __init__(self, role: str, content: str, timestamp: datetime = None, metadata: Dict = None):
        super().__init__()
        self.role = role  # "user", "assistant", "system"
        self.content = content
        self.timestamp = timestamp or datetime.now()
        self.metadata = metadata or {}


class PanelSelected(Message):
    """Panel selection changed."""
    def __init__(self, panel_id: str):
        super().__init__()
        self.panel_id = panel_id


class EntryCaptured(Message):
    """New entry captured from chat input."""
    def __init__(self, text: str, source: str = "Text"):
        super().__init__()
        self.text = text
        self.source = source


# Panel action messages
class NewProjectRequested(Message):
    pass


class ArchiveProjectRequested(Message):
    def __init__(self, project_name: str):
        super().__init__()
        self.project_name = project_name


class EditProjectRequested(Message):
    def __init__(self, project_name: str):
        super().__init__()
        self.project_name = project_name


class ProjectSelected(Message):
    def __init__(self, project_name: str):
        super().__init__()
        self.project_name = project_name


class ToggleTaskRequested(Message):
    def __init__(self, project: str, task_text: str, current_state: bool, line: int):
        super().__init__()
        self.project = project
        self.task_text = task_text
        self.current_state = current_state
        self.line = line


class NewTaskRequested(Message):
    def __init__(self, project: Optional[str] = None):
        super().__init__()
        self.project = project


class SetDueDateRequested(Message):
    def __init__(self, project: str, task_text: str):
        super().__init__()
        self.project = project
        self.task_text = task_text


class TaskSelected(Message):
    def __init__(self, project: str, task_text: str, done: bool, line: int):
        super().__init__()
        self.project = project
        self.task_text = task_text
        self.done = done
        self.line = line


class JournalEntrySelected(Message):
    def __init__(self, date: datetime, entry: dict):
        super().__init__()
        self.date = date
        self.entry = entry


class ReprocessEntryRequested(Message):
    def __init__(self, date: datetime, entry: dict):
        super().__init__()
        self.date = date
        self.entry = entry


class LinkEntryRequested(Message):
    def __init__(self, date: datetime, entry: dict):
        super().__init__()
        self.date = date
        self.entry = entry


class EntryToTaskRequested(Message):
    def __init__(self, date: datetime, entry: dict):
        super().__init__()
        self.date = date
        self.entry = entry


class SearchResultSelected(Message):
    def __init__(self, result: SearchResult):
        super().__init__()
        self.result = result


class ShowFiltersRequested(Message):
    pass


class EditSettingRequested(Message):
    def __init__(self, section: str, key: str, value):
        super().__init__()
        self.section = section
        self.key = key
        self.value = value


class ResetSettingsRequested(Message):
    pass


class NewCalendarBlockRequested(Message):
    def __init__(self, date: datetime):
        super().__init__()
        self.date = date


# ========== Widgets ==========

class ChatMessageWidget(Static):
    """A single chat message bubble."""
    
    def __init__(self, message: ChatMessage, **kwargs):
        super().__init__(**kwargs)
        self.message = message
    
    def compose(self) -> ComposeResult:
        role_style = {
            "user": "bold cyan",
            "assistant": "bold green", 
            "system": "bold yellow",
        }.get(self.message.role, "white")
        
        time_str = self.message.timestamp.strftime("%H:%M")
        
        yield Static(
            f"[{role_style}]{self.message.role.title()}[/{role_style}] [dim]{time_str}[/dim]",
            classes="message-header"
        )
        yield Static(self.message.content, classes="message-content", markup=True)


class ChatHistory(ScrollableContainer):
    """Scrollable chat history."""
    
    BINDINGS = [
        Binding("ctrl+u", "scroll_up", "Scroll Up"),
        Binding("ctrl+d", "scroll_down", "Scroll Down"),
    ]
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.messages: List[ChatMessage] = []
        self._auto_scroll = True
    
    def add_message(self, message: ChatMessage):
        """Add a message to history."""
        self.messages.append(message)
        widget = ChatMessageWidget(message)
        self.mount(widget)
        
        if self._auto_scroll:
            self.scroll_end(animate=False)
    
    def clear(self):
        """Clear all messages."""
        self.messages.clear()
        for child in self.children:
            child.remove()
    
    def action_scroll_up(self):
        self.scroll_up()
        self._auto_scroll = False
    
    def action_scroll_down(self):
        self.scroll_down()
        if self.scroll_y >= self.max_scroll_y - 1:
            self._auto_scroll = True
    
    def on_scroll(self, event):
        """Track scroll position for auto-scroll behavior."""
        if event.scroll_y >= self.max_scroll_y - 1:
            self._auto_scroll = True


class ChatInput(TextArea):
    """Multi-line chat input with keybindings."""
    
    BINDINGS = [
        Binding("enter", "send", "Send"),
        Binding("shift+enter", "newline", "New Line"),
        Binding("ctrl+l", "clear", "Clear"),
        Binding("ctrl+v", "voice", "Voice"),
    ]
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.placeholder = "Message Personal OS... (Enter to send, Shift+Enter for new line)"
        self.show_line_numbers = False
        self.tab_behavior = "indent"
    
    def action_send(self):
        text = self.text.strip()
        if text:
            self.post_message(EntryCaptured(text))
            self.clear()
    
    def action_newline(self):
        self.insert("\n")
    
    def action_clear(self):
        self.clear()


# ========== Main App ==========

class PersonalOSTUI(App):
    """Personal OS Terminal User Interface."""
    
    CSS = """
    /* Layout */
    Screen {
        layout: horizontal;
    }
    
    #sidebar {
        width: 25%;
        min-width: 40;
        max-width: 60;
        border-right: solid $primary;
        background: $surface;
    }
    
    #main {
        width: 75%;
        layout: vertical;
    }
    
    #chat-workspace {
        height: 1fr;
        layout: vertical;
    }
    
    #chat-history {
        height: 1fr;
        border-bottom: solid $primary;
        padding: 1;
        overflow-y: auto;
    }
    
    #chat-input {
        height: 8;
        min-height: 5;
        max-height: 20;
        padding: 1;
    }
    
    /* Panels */
    .panel-title {
        text-style: bold;
        padding: 1;
        border-bottom: solid $primary;
        background: $boost;
    }
    
    .setting {
        padding: 0 1;
        color: $text-muted;
    }
    
    /* Chat messages */
    .message-header {
        margin-bottom: 0;
        padding: 0 1;
    }
    
    .message-content {
        margin: 0 1 1 1;
        padding: 0 1;
    }
    
    /* Sidebar items */
    Tree {
        padding: 0 1;
    }
    
    ListView {
        padding: 0 1;
    }
    
    ListItem {
        padding: 0 1;
    }
    
    ListItem.--highlight {
        background: $primary 20%;
    }
    
    /* Input */
    TextArea {
        border: solid $primary;
    }
    
    TextArea:focus {
        border: solid $accent;
    }
    
    /* Status */
    #status-bar {
        height: 1;
        background: $boost;
        padding: 0 1;
    }
    
    /* Search panel */
    .search-empty {
        color: $text-muted;
        text-style: italic;
    }
    
    .search-file {
        text-style: bold;
        color: $accent;
    }
    
    .search-match {
        color: $text;
    }
    
    .search-more {
        color: $text-muted;
        text-style: italic;
    }
    
    /* Journal panel */
    .journal-date {
        text-style: bold;
    }
    
    .journal-date-empty {
        color: $text-muted;
    }
    
    .journal-entry {
        color: $text;
    }
    
    /* Calendar panel */
    .cal-nav {
        height: 3;
        align: center middle;
    }
    
    .cal-period {
        width: 1fr;
        text-align: center;
        text-style: bold;
    }
    
    .cal-view {
        padding: 1;
        overflow-y: auto;
    }
    
    /* Tasks panel */
    .task-header {
        text-style: bold;
        color: $accent;
    }
    
    .task-section {
        color: $text-muted;
        text-style: italic;
    }
    
    .task-item {
        color: $text;
    }
    
    /* Settings panel */
    .setting-row {
        padding: 0 1;
    }
    
    .setting-item {
        padding: 0 1;
    }
    """
    
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=False),
        Binding("ctrl+p", "command_palette", "Command Palette"),
        Binding("ctrl+b", "toggle_sidebar", "Toggle Sidebar"),
        Binding("tab", "next_panel", "Next Panel"),
        Binding("shift+tab", "prev_panel", "Prev Panel"),
        Binding("f1", "panel('projects')", "Projects", show=False),
        Binding("f2", "panel('tasks')", "Tasks", show=False),
        Binding("f3", "panel('journal')", "Journal", show=False),
        Binding("f4", "panel('search')", "Search", show=False),
        Binding("f5", "panel('calendar')", "Calendar", show=False),
        Binding("f6", "panel('settings')", "Settings", show=False),
        Binding("ctrl+1", "focus_chat", "Focus Chat", show=False),
        Binding("ctrl+2", "focus_sidebar", "Focus Sidebar", show=False),
        Binding("ctrl+f", "focus_search", "Search", show=False),
        Binding("ctrl+n", "new_task", "New Task", show=False),
    ]
    
    TITLE = "Personal OS"
    SUB_TITLE = "Your terminal-native personal operating system"
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.config = get_config()
        self.vault = get_vault()
        self.processor = BackgroundProcessor(self.vault)
        self.current_panel = "projects"
        self._panels = {}
    
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        
        with Horizontal():
            # Sidebar
            with Vertical(id="sidebar"):
                yield ProjectsPanel(self.vault, id="panel-projects")
                yield TasksPanel(self.vault, id="panel-tasks")
                yield JournalPanel(self.vault, id="panel-journal")
                yield SearchPanel(self.vault, id="panel-search")
                yield CalendarPanel(self.vault, id="panel-calendar")
                yield SettingsPanel(self.config, id="panel-settings")
            
            # Main chat workspace
            with Vertical(id="main"):
                with Container(id="chat-workspace"):
                    yield ChatHistory(id="chat-history")
                    yield ChatInput(id="chat-input")
                
                yield Static("Ready", id="status-bar")
    
    def on_mount(self):
        """Initialize on mount."""
        # Hide all panels except projects initially
        self._panels = {
            "projects": self.query_one("#panel-projects"),
            "tasks": self.query_one("#panel-tasks"),
            "journal": self.query_one("#panel-journal"),
            "search": self.query_one("#panel-search"),
            "calendar": self.query_one("#panel-calendar"),
            "settings": self.query_one("#panel-settings"),
        }
        
        self._show_panel("projects")
        
        # Start background processor
        asyncio.create_task(self.processor.start())
        
        # Load initial projects
        asyncio.create_task(self._refresh_projects())
    
    def _show_panel(self, panel_id: str):
        """Show only the selected panel."""
        for pid, panel in self._panels.items():
            panel.display = (pid == panel_id)
        self.current_panel = panel_id
        
        # Refresh content
        panel = self._panels.get(panel_id)
        if panel and hasattr(panel, 'refresh_content'):
            asyncio.create_task(panel.refresh_content())
    
    def action_panel(self, panel_id: str):
        """Switch to panel."""
        if panel_id in self._panels:
            self._show_panel(panel_id)
            self.notify(f"Panel: {panel_id.title()}")
    
    def action_next_panel(self):
        """Cycle to next panel."""
        panels = list(self._panels.keys())
        idx = panels.index(self.current_panel)
        self.action_panel(panels[(idx + 1) % len(panels)])
    
    def action_prev_panel(self):
        """Cycle to previous panel."""
        panels = list(self._panels.keys())
        idx = panels.index(self.current_panel)
        self.action_panel(panels[(idx - 1) % len(panels)])
    
    def action_toggle_sidebar(self):
        """Toggle sidebar visibility."""
        sidebar = self.query_one("#sidebar")
        sidebar.display = not sidebar.display
    
    def action_focus_chat(self):
        """Focus chat input."""
        self.query_one("#chat-input", ChatInput).focus()
    
    def action_focus_sidebar(self):
        """Focus sidebar."""
        panel = self._panels.get(self.current_panel)
        if panel:
            panel.focus()
    
    def action_focus_search(self):
        """Focus search panel."""
        self.action_panel("search")
        search_input = self.query_one("#search-input", Input)
        search_input.focus()
    
    def _add_system_message(self, content: str):
        """Add system message to chat."""
        msg = ChatMessage("system", content)
        self.query_one("#chat-history", ChatHistory).add_message(msg)
    
    def _add_user_message(self, content: str):
        """Add user message to chat."""
        msg = ChatMessage("user", content)
        self.query_one("#chat-history", ChatHistory).add_message(msg)
    
    def _add_assistant_message(self, content: str):
        """Add assistant message to chat."""
        msg = ChatMessage("assistant", content)
        self.query_one("#chat-history", ChatHistory).add_message(msg)
    
    async def on_entry_captured(self, event: EntryCaptured):
        """Handle new entry from chat input."""
        text = event.text
        source = event.source
        
        # Add to chat immediately
        self._add_user_message(text)
        
        # Process in background
        self.query_one("#status-bar", Static).update("Processing...")
        
        try:
            result = await process_entry(text, source)
            await self._handle_processing_result(result, text)
        except Exception as e:
            self._add_assistant_message(f"⚠️ Processing error: {e}")
        finally:
            self.query_one("#status-bar", Static).update("Ready")
    
    async def _handle_processing_result(self, result: ProcessingResult, original_text: str):
        """Handle processing result and respond in chat."""
        if not result.changes_made:
            self._add_assistant_message("📝 Logged. No actionable items detected.")
            return
        
        # Summarize changes
        changes = result.changes_made
        if len(changes) == 1:
            self._add_assistant_message(f"✅ {changes[0]}")
        else:
            summary = "✅ Processed:\n" + "\n".join(f"  • {c}" for c in changes[:5])
            if len(changes) > 5:
                summary += f"\n  • ... and {len(changes) - 5} more"
            self._add_assistant_message(summary)
        
        # Refresh relevant panels
        await self._refresh_projects()
    
    async def _refresh_projects(self):
        """Refresh projects panel."""
        panel = self._panels.get("projects")
        if panel and hasattr(panel, 'refresh_content'):
            await panel.refresh_content()
    
    # ========== Panel Action Handlers ==========
    
    def on_new_project_requested(self, event: NewProjectRequested):
        """Handle new project request."""
        # Could show modal for name/description
        self._add_system_message("New project creation coming soon — just type it in chat!")
    
    async def on_archive_project_requested(self, event: ArchiveProjectRequested):
        """Handle archive project request."""
        project_dir = self.vault.config.projects_dir / event.project_name
        if project_dir.exists():
            # Move to archive
            archive_dir = self.vault.config.root / "06-Archive" / event.project_name
            import shutil
            shutil.move(str(project_dir), str(archive_dir))
            self._add_system_message(f"📦 Archived {event.project_name}")
            await self._refresh_projects()
        else:
            self._add_assistant_message(f"❌ Project not found: {event.project_name}")
    
    def on_edit_project_requested(self, event: EditProjectRequested):
        """Handle edit project request."""
        self._add_system_message(f"Project editing coming soon for {event.project_name}")
    
    def on_project_selected(self, event: ProjectSelected):
        """Handle project selection from panel."""
        self._add_system_message(f"📁 Selected project: {event.project_name}")
        # Could open project details in main area
    
    async def on_toggle_task_requested(self, event: ToggleTaskRequested):
        """Handle task toggle."""
        tasks_file = self.vault.config.projects_dir / event.project / "Tasks.md"
        if tasks_file.exists():
            content = self.vault.read_file(tasks_file)
            lines = content.split('\n')
            if 0 <= event.line - 1 < len(lines):
                line = lines[event.line - 1]
                if event.current_state:
                    lines[event.line - 1] = line.replace("- [x]", "- [ ]")
                else:
                    lines[event.line - 1] = line.replace("- [ ]", "- [x]")
                
                self.vault.write_file(tasks_file, '\n'.join(lines))
                new_state = "done" if not event.current_state else "open"
                self._add_assistant_message(f"✅ Task marked {new_state}: {event.task_text[:50]}")
                await self._refresh_projects()
            else:
                self._add_assistant_message("❌ Could not find task line")
        else:
            self._add_assistant_message(f"❌ No Tasks.md for {event.project}")
    
    def on_new_task_requested(self, event: NewTaskRequested):
        """Handle new task request."""
        project = event.project
        if not project:
            # Get current project from tasks panel
            tasks_panel = self._panels.get("tasks")
            if tasks_panel:
                # Could get from selection
                pass
        
        if project:
            self._add_system_message(f"📝 New task for {project} — just type it in chat!")
        else:
            self._add_system_message("📝 New task — just type it in chat!")
    
    def on_set_due_date_requested(self, event: SetDueDateRequested):
        """Handle set due date request."""
        self._add_system_message(f"📅 Due date for '{event.task_text[:40]}' — coming soon!")
    
    def on_task_selected(self, event: TaskSelected):
        """Handle task selection."""
        self._add_system_message(f"📋 Task: {event.task_text[:60]} ({'done' if event.done else 'open'})")
    
    def on_journal_entry_selected(self, event: JournalEntrySelected):
        """Handle journal entry selection."""
        entry = event.entry
        self._add_system_message(f"📖 {entry['time']} [{entry['source']}]: {entry['content'][:100]}")
    
    async def on_reprocess_entry_requested(self, event: ReprocessEntryRequested):
        """Handle reprocess entry request."""
        self._add_system_message("🔄 Reprocessing entry...")
        result = await process_entry(event.entry['content'], event.entry['source'])
        for c in result.changes_made:
            self._add_assistant_message(f"  {c}")
    
    def on_link_entry_requested(self, event: LinkEntryRequested):
        """Handle link entry to project request."""
        self._add_system_message("🔗 Link to project — coming soon!")
    
    def on_entry_to_task_requested(self, event: EntryToTaskRequested):
        """Handle convert entry to task."""
        self._add_system_message("✅ Convert to task — coming soon!")
    
    def on_search_result_selected(self, event: SearchResultSelected):
        """Handle search result selection."""
        result = event.result
        self._add_system_message(f"🔍 {result.file}:{result.line} — {result.content[:80]}")
        # Could open file in editor
    
    def on_show_filters_requested(self, event: ShowFiltersRequested):
        """Handle show filters request."""
        self._add_system_message("🔧 Filters: project:, type:, tag:, date:, -exclude")
    
    def on_edit_setting_requested(self, event: EditSettingRequested):
        """Handle edit setting request."""
        self._add_system_message(f"⚙️ Setting {event.section}.{event.key} = {event.value} — edit via config file")
    
    def on_reset_settings_requested(self, event: ResetSettingsRequested):
        """Handle reset settings request."""
        self._add_system_message("⚙️ Reset settings — edit ~/.config/personal-os/config.toml")
    
    def on_new_calendar_block_requested(self, event: NewCalendarBlockRequested):
        """Handle new calendar block request."""
        self._add_system_message(f"📅 New block for {event.date.strftime('%Y-%m-%d')} — coming soon!")
    
    # ========== Other Actions ==========
    
    async def action_new_task(self):
        """Quick task creation."""
        self._add_system_message("📝 Task creation — just type it in chat!")
    
    async def action_command_palette(self):
        """Show command palette."""
        self._add_system_message("🎯 Command palette coming soon (Ctrl+P)")
    
    def on_unmount(self):
        """Cleanup on exit."""
        asyncio.create_task(self.processor.stop())


# ========== Entry Points ==========

def run_tui():
    """Run the TUI application."""
    app = PersonalOSTUI()
    app.run()


def run_cli(args: List[str]):
    """Run CLI commands (capture, voice, etc.) without TUI."""
    import sys
    from personal_os.cli.main import cli
    sys.argv = ["pos"] + args
    cli()


if __name__ == "__main__":
    run_tui()