"""SearchResults — reusable, embeddable search results component.

Shows search results grouped by file with context snippets.
Supports keyboard navigation and result selection.
"""
from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional

from textual.app import ComposeResult
from textual.containers import Vertical, ScrollableContainer, Horizontal
from textual.widgets import Static, Label, Input
from textual.message import Message
from textual.binding import Binding

from personal_os.tui.components.base import Component
from personal_os.tui.providers.search_provider import SearchResultsData, SearchResultItem
from personal_os.tui.components.tag_colors import color_for


class SearchResultRow(Static):
    """A single search result row."""

    DEFAULT_CSS = """
    SearchResultRow {
        layout: horizontal;
        height: 1;
        padding: 0 1;
        background: transparent;
    }
    SearchResultRow:hover {
        background: $boost;
    }
    SearchResultRow.selected {
        background: $accent 30%;
    }
    SearchResultRow .result-file {
        color: $primary;
        width: 36;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    SearchResultRow .result-line {
        color: $text-muted;
        width: 6;
        text-align: right;
    }
    SearchResultRow .result-content {
        color: $text;
        width: 1fr;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    SearchResultRow .result-project {
        color: $text-muted;
        width: 16;
        text-align: right;
    }
    SearchResultRow .result-type {
        color: $accent;
        width: 10;
        text-align: right;
        text-style: bold;
    }
    """

    def __init__(
        self,
        item: SearchResultItem,
        is_selected: bool = False,
        callback: Optional[Callable[[SearchResultItem], None]] = None,
        **kwargs
    ):
        kwargs.setdefault("classes", "search-result-row")
        super().__init__(**kwargs)
        self.item = item
        self.is_selected = is_selected
        self.callback = callback

        if is_selected:
            self.add_class("selected")

    def compose(self) -> ComposeResult:
        # File name
        file_label = Label(self.item.file, classes="result-file")
        file_label.styles.color = color_for(self.item.file)
        yield file_label

        # Line number
        yield Label(f"L{self.item.line}", classes="result-line")

        # Content
        content = self.item.content.strip()
        if self.item.context:
            content = self.item.context.strip().replace('\n', ' ')[:100]
        yield Label(content, classes="result-content")

        # Project
        if self.item.project:
            project_label = Label(f"[{self.item.project}]", classes="result-project")
            project_label.styles.color = color_for(self.item.project)
            yield project_label
        else:
            yield Label("", classes="result-project")

        # Type
        if self.item.file_type:
            yield Label(self.item.file_type.upper(), classes="result-type")
        else:
            yield Label("", classes="result-type")

    def on_click(self, event) -> None:
        """Handle click to select result."""
        if self.callback:
            self.callback(self.item)


class SearchInput(Static):
    """Search input with live filtering."""
    
    DEFAULT_CSS = """
    SearchInput {
        layout: horizontal;
        height: 3;
        padding: 1;
        border: round $primary;
        background: $panel;
    }
    SearchInput:focus-within {
        border: round $accent;
    }
    SearchInput Input {
        width: 1fr;
        border: none;
        background: transparent;
    }
    SearchInput .search-icon {
        color: $primary;
        padding: 0 1;
    }
    SearchInput .filter-hint {
        color: $text-muted;
        padding: 0 1;
    }
    """
    
    def __init__(
        self,
        placeholder: str = "Search vault... (project:foo type:progress tag:#bug)",
        on_change: Optional[Callable[[str], None]] = None,
        on_submit: Optional[Callable[[str], None]] = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        self._placeholder = placeholder
        self.on_change = on_change
        self.on_submit = on_submit
    
    def compose(self) -> ComposeResult:
        yield Static("🔍", classes="search-icon")
        input_widget = Input(placeholder=self._placeholder, id="search-input")
        yield input_widget
        yield Static("project: type: tag: date: -exclude", classes="filter-hint")
    
    def on_mount(self) -> None:
        self.query_one("#search-input", Input).focus()
    
    def on_input_changed(self, event: Input.Changed) -> None:
        if self.on_change:
            self.on_change(event.value)
    
    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self.on_submit:
            self.on_submit(event.value)
    
    def get_value(self) -> str:
        return self.query_one("#search-input", Input).value
    
    def set_value(self, value: str):
        self.query_one("#search-input", Input).value = value


class SearchResults(Component):
    """An embeddable search results panel."""

    component_id = "search-results"
    title = "Search Results"

    BINDINGS = [
        Binding("enter", "select_result", "Open"),
        Binding("p", "pin_result", "Pin"),
        Binding("f", "show_filter_menu", "Filters"),
        Binding("escape", "clear_search", "Clear"),
    ]

    DEFAULT_CSS = """
    SearchResults {
        layout: vertical;
    }
    SearchResults .results-header {
        color: $primary;
        text-style: bold;
        padding: 1 0;
    }
    SearchResults .results-empty {
        color: $text-muted;
        text-align: center;
        padding: 4;
    }
    SearchResults .results-searching {
        color: $warning;
        text-align: center;
        padding: 2;
    }
    SearchResults .results-error {
        color: $error;
        text-align: center;
        padding: 2;
    }
    SearchResults .results-count {
        color: $text-muted;
        padding: 1 0;
    }
    SearchResults .results-more {
        color: $text-muted;
        padding: 0 1 1 1;
        text-style: italic;
    }
    SearchResults .result-file-header {
        color: $primary;
        text-style: bold;
        padding: 1 1 0 1;
    }
    SearchResults .filter-chips {
        layout: horizontal;
        height: auto;
        padding: 1;
    }
    SearchResults .filter-chip {
        border: solid $primary;
        padding: 0 1;
        margin: 0 1;
        background: $panel;
    }
    SearchResults .filter-chip.active {
        background: $primary;
        color: $background;
    }
    """

    def __init__(
        self,
        context=None,
        on_select: Optional[Callable[[SearchResultItem], None]] = None,
        on_search_change: Optional[Callable[[str], None]] = None,
        on_search_submit: Optional[Callable[[str], None]] = None,
        **kwargs
    ):
        super().__init__(context=context, **kwargs)
        self._data: Optional[SearchResultsData] = None
        self.on_select = on_select
        self.on_search_change = on_search_change
        self.on_search_submit = on_search_submit
        self._selected_index = 0
        self._filter_chips = {}

    def update_data(self, data: SearchResultsData):
        """Update panel with new search data."""
        self._data = data
        self.call_later(self.recompose)

    def compose(self) -> ComposeResult:
        # Search input
        yield SearchInput(
            on_change=self._on_search_change,
            on_submit=self._on_search_submit
        )
        
        # Filter chips (if any active filters in query)
        if self._data and self._data.query:
            filters = self._parse_filters_from_query(self._data.query)
            if filters:
                yield self._render_filter_chips(filters)

        # Header with count/status
        if self._data is None:
            yield Static("Enter a query to search the vault", classes="results-empty")
            return

        if self._data.is_searching:
            yield Static("🔍 Searching...", classes="results-searching")
            return

        if self._data.error:
            yield Static(f"❌ Error: {self._data.error}", classes="results-error")
            return

        if not self._data.results:
            yield Static(f"No results for '{self._data.query}'", classes="results-empty")
            return

        # Results count
        count_text = f"{len(self._data.results)} result{'s' if len(self._data.results) != 1 else ''} for '{self._data.query}'"
        yield Static(count_text, classes="results-count")

        # Results grouped by file
        with ScrollableContainer():
            grouped = self._data.group_by_file()
            for file_path, items in grouped.items():
                # File header
                yield Static(f"📄 {file_path} ({len(items)} match{'es' if len(items) != 1 else ''})", classes="result-file-header")
                
                # Results
                for i, item in enumerate(items[:10]):  # Limit per file
                    is_last = (i == len(items[:10]) - 1) and len(items) <= 10
                    row = SearchResultRow(
                        item,
                        callback=self._handle_select
                    )
                    yield row
                
                if len(items) > 10:
                    yield Static(f"  ... and {len(items) - 10} more in this file", classes="results-more")

    def _on_search_change(self, query: str):
        """Debounced search on input change."""
        if self.on_search_change:
            self.on_search_change(query)

    def _on_search_submit(self, query: str):
        """Handle search submit."""
        if self.on_search_submit:
            self.on_search_submit(query)

    def _handle_select(self, item: SearchResultItem):
        """Handle result selection."""
        if self.on_select:
            self.on_select(item)

    def _parse_filters_from_query(self, query: str) -> Dict[str, str]:
        """Parse filter chips from query string."""
        filters = {}
        parts = query.split()
        
        for part in parts:
            if part.startswith("project:"):
                filters["project"] = part[8:]
            elif part.startswith("type:"):
                filters["type"] = part[5:]
            elif part.startswith("tag:") or part.startswith("#"):
                tag = part[4:] if part.startswith("tag:") else part[1:]
                filters["tag"] = tag
            elif part.startswith("date:"):
                filters["date"] = part[5:]
            elif part.startswith("-"):
                filters.setdefault("exclude", []).append(part[1:])
        
        return filters

    def _render_filter_chips(self, filters: Dict[str, str]) -> Horizontal:
        """Render filter chips as horizontal container."""
        from textual.containers import Horizontal
        from textual.widgets import Static
        
        chips = []
        
        for filter_type, value in filters.items():
            if filter_type == "exclude":
                for excl in value:
                    chips.append(Static(f"✕ {excl}", classes="filter-chip"))
            else:
                label = filter_type.title() if filter_type != "tag" else "Tag"
                chips.append(Static(f"{label}: {value}", classes="filter-chip"))
        
        return Horizontal(*chips, classes="filter-chips")

    def action_select_result(self) -> None:
        """Handle Enter key - select current result."""
        # Get the currently focused result row
        rows = self.query("SearchResultRow")
        if rows and self._selected_index < len(rows):
            row = list(rows)[self._selected_index]
            self._handle_select(row.item)

    def action_pin_result(self) -> None:
        """Handle 'p' key - pin current result."""
        if self.app:
            self.app.notify("Pin not yet implemented")

    def action_show_filter_menu(self) -> None:
        """Handle 'f' key - show filter menu."""
        if self.app:
            self.app.notify("Filter menu not yet implemented")

    def action_clear_search(self) -> None:
        """Handle Escape key - clear search."""
        input_widget = self.query_one("#search-input", Input)
        input_widget.value = ""
        if self.on_search_change:
            self.on_search_change("")


class ResultFileHeader(Static):
    """Header for a file group in results."""
    DEFAULT_CSS = """
    ResultFileHeader {
        color: $primary;
        text-style: bold;
        padding: 1 1 0 1;
    }
    """