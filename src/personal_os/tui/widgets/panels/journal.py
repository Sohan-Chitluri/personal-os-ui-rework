"""Journal panel - date navigator with entry cards."""
from __future__ import annotations
from datetime import datetime, timedelta
from textual.widgets import ListView, ListItem, Label, Static, Input
from textual.message import Message
from textual.binding import Binding

from personal_os.tui.widgets.panels.base import SidebarPanel


class JournalEntrySelected(Message):
    """Posted when a journal entry is selected."""
    def __init__(self, date: datetime, entry_data: dict):
        super().__init__()
        self.date = date
        self.entry_data = entry_data


class JournalPanel(SidebarPanel):
    """Journal navigator with date tree and entry cards."""
    
    BINDINGS = [
        Binding("enter", "select", "Open"),
        Binding("p", "process", "Process"),
        Binding("l", "link", "Link Project"),
        Binding("t", "task", "To Task"),
        Binding("r", "refresh", "Refresh"),
        Binding("left", "prev_day", "Prev Day"),
        Binding("right", "next_day", "Next Day"),
    ]
    
    def __init__(self, vault, **kwargs):
        super().__init__("Journal", "journal", vault=vault, **kwargs)
        self.current_date = datetime.now()
    
    def compose(self):
        yield Static("Journal", classes="panel-title")
        yield Input(placeholder="Jump to date (YYYY-MM-DD)...", id="date-jump")
        yield ListView(id="journal-list")
    
    async def refresh_content(self):
        list_view = self.query_one("#journal-list", ListView)
        list_view.clear()
        
        # Show last 14 days by default
        for i in range(14):
            date = datetime.now() - timedelta(days=i)
            await self._add_date_entries(list_view, date)
    
    async def _add_date_entries(self, list_view: ListView, date: datetime):
        """Add entries for a specific date."""
        entries = self.vault.get_journal_entries(date)
        
        if not entries:
            # Still show date header for empty days
            date_str = date.strftime("%Y-%m-%d")
            if date.date() == datetime.now().date():
                date_str += " (Today)"
            elif date.date() == (datetime.now() - timedelta(days=1)).date():
                date_str += " (Yesterday)"
            
            header = ListItem(Label(f"📅 {date_str} — empty", classes="journal-date-empty"))
            header.data = {"type": "date", "date": date, "empty": True}
            await list_view.append(header)
            return
        
        date_str = date.strftime("%Y-%m-%d")
        if date.date() == datetime.now().date():
            date_str += " (Today)"
        elif date.date() == (datetime.now() - timedelta(days=1)).date():
            date_str += " (Yesterday)"
        
        # Date header
        header = ListItem(Label(f"📅 {date_str} ({len(entries)} entries)", classes="journal-date"))
        header.data = {"type": "date", "date": date, "count": len(entries)}
        await list_view.append(header)
        
        # Entries
        for entry in entries:
            time_str = entry['time']
            source = entry['source']
            content_preview = entry['content'].strip()[:80]
            if len(entry['content'].strip()) > 80:
                content_preview += "..."
            
            item = ListItem(Label(f"  {time_str} [{source}] {content_preview}", classes="journal-entry"))
            item.data = {"type": "entry", "date": date, "entry": entry}
            await list_view.append(item)
    
    def on_list_view_selected(self, event: ListView.Selected):
        """Handle entry selection."""
        if event.item.data and event.item.data.get("type") == "entry":
            data = event.item.data
            self.post_message(JournalEntrySelected(data["date"], data["entry"]))
    
    def action_select(self):
        """Open selected entry."""
        list_view = self.query_one("#journal-list", ListView)
        if list_view.highlighted_child and list_view.highlighted_child.data:
            data = list_view.highlighted_child.data
            if data.get("type") == "entry":
                self.post_message(JournalEntrySelected(data["date"], data["entry"]))
    
    def action_process(self):
        """Re-process selected entry."""
        list_view = self.query_one("#journal-list", ListView)
        if list_view.highlighted_child and list_view.highlighted_child.data:
            data = list_view.highlighted_child.data
            if data.get("type") == "entry":
                self.app.post_message(self.app.ReprocessEntryRequested(
                    data["date"], data["entry"]
                ))
    
    def action_link(self):
        """Link entry to project."""
        list_view = self.query_one("#journal-list", ListView)
        if list_view.highlighted_child and list_view.highlighted_child.data:
            data = list_view.highlighted_child.data
            if data.get("type") == "entry":
                self.app.post_message(self.app.LinkEntryRequested(
                    data["date"], data["entry"]
                ))
    
    def action_task(self):
        """Convert entry to task."""
        list_view = self.query_one("#journal-list", ListView)
        if list_view.highlighted_child and list_view.highlighted_child.data:
            data = list_view.highlighted_child.data
            if data.get("type") == "entry":
                self.app.post_message(self.app.EntryToTaskRequested(
                    data["date"], data["entry"]
                ))
    
    def action_refresh(self):
        """Refresh journal."""
        self.app.run_worker(self.refresh_content())
    
    def action_prev_day(self):
        """Go to previous day."""
        self.current_date -= timedelta(days=1)
        self.app.run_worker(self.refresh_content())
    
    def action_next_day(self):
        """Go to next day."""
        self.current_date += timedelta(days=1)
        self.app.run_worker(self.refresh_content())
    
    async def on_input_submitted(self, event: Input.Submitted):
        """Jump to specific date."""
        try:
            date = datetime.strptime(event.value, "%Y-%m-%d")
            self.current_date = date
            self.app.run_worker(self.refresh_content())
            event.input.clear()
        except ValueError:
            self.app.notify("Invalid date format. Use YYYY-MM-DD", severity="error")