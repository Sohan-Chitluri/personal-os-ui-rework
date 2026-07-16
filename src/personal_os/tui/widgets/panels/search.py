"""Search panel - live search with filters."""
from __future__ import annotations
from textual.widgets import ListView, ListItem, Label, Input, Static
from textual.message import Message
from textual.binding import Binding

from personal_os.tui.widgets.panels.base import SidebarPanel
from personal_os.core.search import search, SearchResult


class SearchResultSelected(Message):
    """Posted when a search result is selected."""
    def __init__(self, result: SearchResult):
        super().__init__()
        self.result = result


class SearchPanel(SidebarPanel):
    """Live search panel with filters."""
    
    BINDINGS = [
        Binding("enter", "open", "Open"),
        Binding("p", "pin", "Pin"),
        Binding("f", "filter", "Filters"),
        Binding("r", "refresh", "Refresh"),
        Binding("escape", "clear", "Clear"),
    ]
    
    def __init__(self, vault, **kwargs):
        super().__init__("Search", "search", vault=vault, **kwargs)
        self.last_query = ""
        self.last_results = []
    
    def compose(self):
        yield Static("Search", classes="panel-title")
        yield Input(placeholder="Search vault... (project:foo type:progress tag:#bug)", id="search-input")
        yield ListView(id="search-results")
    
    async def on_input_changed(self, event: Input.Changed):
        """Live search as user types."""
        if len(event.value) >= 2 and event.value != self.last_query:
            self.last_query = event.value
            await self._do_search(event.value)
    
    async def _do_search(self, query: str):
        """Perform search and update results."""
        try:
            results = await search(query, limit=50)
            self.last_results = results
            await self._update_results(results)
        except Exception as e:
            self.app.notify(f"Search error: {e}", severity="error")
    
    async def _update_results(self, results: list):
        """Update results list."""
        list_view = self.query_one("#search-results", ListView)
        list_view.clear()
        
        if not results:
            item = ListItem(Label("📭 No matches found", classes="search-empty"))
            item.data = {"type": "empty"}
            await list_view.append(item)
            return
        
        # Group by file
        from collections import defaultdict
        by_file = defaultdict(list)
        for r in results:
            by_file[r.file].append(r)
        
        for file_path, matches in by_file.items():
            # File header
            header = ListItem(Label(f"📄 {file_path} ({len(matches)} matches)", classes="search-file"))
            header.data = {"type": "file", "file": file_path}
            await list_view.append(header)
            
            # Matches
            for match in matches[:5]:  # Limit per file
                item = ListItem(Label(f"  L{match.line}: {match.content[:100]}", classes="search-match"))
                item.data = {"type": "match", "result": match}
                await list_view.append(item)
            
            if len(matches) > 5:
                more = ListItem(Label(f"  ... and {len(matches) - 5} more", classes="search-more"))
                more.data = {"type": "more", "file": file_path}
                await list_view.append(more)
    
    def on_list_view_selected(self, event: ListView.Selected):
        """Handle result selection."""
        if event.item.data and event.item.data.get("type") == "match":
            self.post_message(SearchResultSelected(event.item.data["result"]))
    
    def action_open(self):
        """Open selected result."""
        list_view = self.query_one("#search-results", ListView)
        if list_view.highlighted_child and list_view.highlighted_child.data:
            data = list_view.highlighted_child.data
            if data.get("type") == "match":
                self.post_message(SearchResultSelected(data["result"]))
    
    def action_pin(self):
        """Pin search results."""
        # Could implement pinning to sidebar
        self.app.notify(f"Pinned: {self.last_query}")
    
    def action_filter(self):
        """Show filter options."""
        self.app.post_message(self.app.ShowFiltersRequested())
    
    def action_refresh(self):
        """Refresh search."""
        if self.last_query:
            self.app.run_worker(self._do_search(self.last_query))
    
    def action_clear(self):
        """Clear search."""
        input_widget = self.query_one("#search-input", Input)
        input_widget.clear()
        self.last_query = ""
        self.last_results = []
    
    def on_input_submitted(self, event: Input.Submitted):
        """Handle enter in search input."""
        if event.value:
            self.last_query = event.value
            self.app.run_worker(self._do_search(event.value))