"""JournalEntry — reusable, embeddable journal entry component.

Shows a timestamped capture with original text and collapsible semantic metadata.
The raw capture text is PRIMARY and never replaced or summarized.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, ScrollableContainer
from textual.widgets import Static, Label
from textual.message import Message

from personal_os.tui.components.base import Component
from personal_os.tui.providers.journal_provider import JournalEntry as ProviderJournalEntry, SemanticItem
from personal_os.tui.components.tag_colors import color_for


class JournalEntryWidget(Static):
    """A single journal entry showing raw capture and optional semantic metadata."""

    DEFAULT_CSS = """
    JournalEntryWidget {
        layout: vertical;
        border: round $border;
        background: $surface;
        margin: 1 0;
        padding: 1;
    }
    JournalEntryWidget .entry-header {
        layout: horizontal;
        height: 1;
        padding: 0 1;
        margin-bottom: 1;
    }
    JournalEntryWidget .entry-time {
        color: $primary;
        text-style: bold;
        width: 10;
    }
    JournalEntryWidget .entry-source {
        color: $accent;
        width: 10;
        text-align: center;
    }
    JournalEntryWidget .entry-capture-id {
        color: $text-muted;
        width: 24;
        text-align: right;
    }
    JournalEntryWidget .entry-content {
        color: $text;
        padding: 1;
        border-left: solid $primary;
        margin-left: 1;
        background: $background;
    }
    JournalEntryWidget .entry-actions {
        layout: horizontal;
        height: 1;
        padding: 0 1;
        margin-top: 1;
        border-top: solid $border;
    }
    JournalEntryWidget .entry-actions .action-btn {
        color: $primary;
        text-style: bold;
        margin-right: 2;
    }
    JournalEntryWidget .entry-actions .action-btn:hover {
        color: $accent;
    }
    JournalEntryWidget .semantic-toggle {
        layout: horizontal;
        height: 1;
        padding: 1;
        margin-top: 1;
        border-top: solid $border;
    }
    JournalEntryWidget .semantic-toggle .toggle-btn {
        color: $primary;
        text-style: bold;
    }
    JournalEntryWidget .semantic-toggle .toggle-btn:hover {
        color: $accent;
    }
    JournalEntryWidget .semantic-content {
        margin-top: 1;
        padding: 1;
        border: round $border;
        background: $panel;
    }
    JournalEntryWidget .semantic-item {
        margin: 1 0;
        padding: 1;
        border: round $border;
        background: $background;
    }
    JournalEntryWidget .semantic-type {
        color: $accent;
        text-style: bold;
    }
    JournalEntryWidget .semantic-project {
        color: $text-muted;
    }
    JournalEntryWidget .semantic-description {
        color: $text;
        margin-top: 1;
    }
    JournalEntryWidget .semantic-meta {
        color: $text-muted;
        text-style: dim;
        margin-top: 1;
    }
    """

    def __init__(
        self,
        entry: ProviderJournalEntry,
        is_expanded: bool = False,
        on_toggle: Optional[Callable[[str], None]] = None,
        on_reprocess: Optional[Callable[[str], None]] = None,
        on_link_project: Optional[Callable[[str], None]] = None,
        on_convert_task: Optional[Callable[[str], None]] = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.entry = entry
        self.is_expanded = is_expanded
        self.on_toggle = on_toggle
        self.on_reprocess = on_reprocess
        self.on_link_project = on_link_project
        self.on_convert_task = on_convert_task

    def compose(self) -> ComposeResult:
        # Header
        with Horizontal(classes="entry-header"):
            yield Label(self.entry.time, classes="entry-time")
            source_label = Label(f"[{self.entry.source}]", classes="entry-source")
            if self.entry.source.lower() == "voice":
                source_label.styles.color = "#fbbf24"
            else:
                source_label.styles.color = color_for(self.entry.source)
            yield source_label
            if self.entry.capture_id:
                yield Label(f"cid: {self.entry.capture_id[:8]}", classes="entry-capture-id")

        # Raw capture content (PRIMARY - always visible)
        yield Static(self.entry.content.strip(), classes="entry-content")

        # Action buttons
        if self.entry.capture_id:
            with Horizontal(classes="entry-actions"):
                if self.on_reprocess:
                    yield Static("Re-process", classes="action-btn", id=f"reprocess-{self.entry.capture_id}")
                if self.on_link_project:
                    yield Static("Link to Project", classes="action-btn", id=f"link-{self.entry.capture_id}")
                if self.on_convert_task:
                    yield Static("Convert to Task", classes="action-btn", id=f"convert-{self.entry.capture_id}")

        # Semantic toggle if available
        if self.entry.has_semantic and self.entry.capture_id:
            with Horizontal(classes="semantic-toggle"):
                toggle_text = "▼ Hide Semantic" if self.is_expanded else "▶ Show Semantic"
                yield Static(toggle_text, classes="toggle-btn", id=f"toggle-{self.entry.capture_id}")

            # Semantic content (collapsible)
            if self.is_expanded:
                with Vertical(classes="semantic-content"):
                    for item in self.entry.semantic_items:
                        with Vertical(classes="semantic-item"):
                            yield Static(f"{item.type}: {item.description}", classes="semantic-description")
                            meta_parts = []
                            if item.project:
                                meta_parts.append(f"project: {item.project}")
                            if item.temporal_context:
                                meta_parts.append(f"time: {item.temporal_context}")
                            if item.certainty > 0:
                                meta_parts.append(f"certainty: {item.certainty:.2f}")
                            if item.confidence > 0:
                                meta_parts.append(f"confidence: {item.confidence:.2f}")
                            if item.action:
                                meta_parts.append(f"action: {item.action}")
                            if meta_parts:
                                yield Static(" | ".join(meta_parts), classes="semantic-meta")

    def on_click(self, event) -> None:
        """Handle click on semantic toggle and action buttons."""
        if not self.entry.capture_id:
            return
            
        target_id = event.widget.id if event.widget else None
        if target_id:
            if target_id.startswith("toggle-") and self.on_toggle:
                self.on_toggle(self.entry.capture_id)
            elif target_id.startswith("reprocess-") and self.on_reprocess:
                self.on_reprocess(self.entry.capture_id)
            elif target_id.startswith("link-") and self.on_link_project:
                self.on_link_project(self.entry.capture_id)
            elif target_id.startswith("convert-") and self.on_convert_task:
                self.on_convert_task(self.entry.capture_id)


class JournalDateHeader(Static):
    """Header for a day's journal entries."""

    DEFAULT_CSS = """
    JournalDateHeader {
        color: $accent;
        text-style: bold;
        padding: 1 0;
        border-bottom: solid $border;
        margin-bottom: 1;
    }
    JournalDateHeader .date-label {
        color: $primary;
    }
    JournalDateHeader .entry-count {
        color: $text-muted;
    }
    """

    def __init__(
        self,
        date: datetime,
        entry_count: int,
        is_today: bool = False,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.date = date
        self.entry_count = entry_count
        self.is_today = is_today

    def render(self) -> str:
        date_str = self.date.strftime("%A, %d %B %Y")
        if self.is_today:
            date_str += " (Today)"
        elif (datetime.now().date() - self.date.date()).days == 1:
            date_str += " (Yesterday)"
        
        return f"[bold]{date_str}[/]  [dim]{self.entry_count} entr{'y' if self.entry_count == 1 else 'ies'}[/]"


class JournalEmptyState(Static):
    """Empty state for journal."""

    DEFAULT_CSS = """
    JournalEmptyState {
        color: $text-muted;
        text-align: center;
        padding: 4;
    }
    """

    def render(self) -> str:
        return "No journal entries for this period.\nCapture something with Ctrl+I to get started."


class JournalList(Component):
    """An embeddable journal list component showing entries by date."""

    component_id = "journal-list"
    title = "Journal"

    DEFAULT_CSS = """
    JournalList {
        layout: vertical;
    }
    JournalList .list-container {
        height: 1fr;
        overflow-y: auto;
    }
    JournalList .date-section {
        margin-bottom: 1;
    }
    """

    def __init__(
        self,
        context=None,
        on_entry_select: Optional[Callable[[ProviderJournalEntry], None]] = None,
        on_toggle_semantic: Optional[Callable[[str], None]] = None,
        on_reprocess: Optional[Callable[[str], None]] = None,
        on_link_project: Optional[Callable[[str], None]] = None,
        on_convert_task: Optional[Callable[[str], None]] = None,
        **kwargs
    ):
        super().__init__(context=context, **kwargs)
        self._data = None
        self.on_entry_select = on_entry_select
        self.on_toggle_semantic = on_toggle_semantic
        self.on_reprocess = on_reprocess
        self.on_link_project = on_link_project
        self.on_convert_task = on_convert_task

    def update_data(self, journal_data):
        """Update with new journal data."""
        self._data = journal_data
        self.call_later(self.recompose)

    def compose(self) -> ComposeResult:
        if not self._data or not self._data.days:
            yield JournalEmptyState()
            return

        with Vertical(classes="list-container"):
            for day_data in self._data.days:
                is_today = (datetime.now().date() == day_data.date.date())
                is_selected = self._data.selected_date and self._data.selected_date.date() == day_data.date.date()
                
                with Vertical(classes="date-section"):
                    # Date header
                    yield JournalDateHeader(day_data.date, day_data.entry_count, is_today)
                    
                    # Entries
                    for entry in day_data.entries:
                        is_expanded = self._data.is_expanded(entry.capture_id) if entry.capture_id else False
                        yield JournalEntryWidget(
                            entry,
                            is_expanded=is_expanded,
                            on_toggle=self._handle_toggle,
                            on_reprocess=self.on_reprocess,
                            on_link_project=self.on_link_project,
                            on_convert_task=self.on_convert_task
                        )

    def _handle_toggle(self, capture_id: str):
        """Handle semantic toggle for an entry."""
        if self._data:
            new_state = self._data.toggle_expanded(capture_id)
            self.call_later(self.recompose)


class JournalDateNavigator(Component):
    """Date navigator sidebar for journal."""

    component_id = "journal-date-nav"
    title = "Dates"

    DEFAULT_CSS = """
    JournalDateNavigator {
        layout: vertical;
    }
    JournalDateNavigator .nav-header {
        color: $primary;
        text-style: bold;
        padding: 1 0;
    }
    JournalDateNavigator .date-item {
        layout: horizontal;
        height: 1;
        padding: 0 1;
        background: transparent;
    }
    JournalDateNavigator .date-item:hover {
        background: $boost;
    }
    JournalDateNavigator .date-item.selected {
        background: $accent 30%;
    }
    JournalDateNavigator .date-label {
        color: $text;
        width: 1fr;
    }
    JournalDateNavigator .date-count {
        color: $text-muted;
        width: 8;
        text-align: right;
    }
    JournalDateNavigator .date-today {
        color: $accent;
        text-style: bold;
    }
    """

    def __init__(
        self,
        context=None,
        on_date_select: Optional[Callable[[datetime], None]] = None,
        **kwargs
    ):
        super().__init__(context=context, **kwargs)
        self._dates: List[datetime] = []
        self._entry_counts: Dict[datetime, int] = {}
        self._selected_date: Optional[datetime] = None
        self.on_date_select = on_date_select

    def update_data(self, journal_data):
        """Update with new journal data."""
        if journal_data:
            self._dates = [day.date for day in journal_data.days]
            self._entry_counts = {day.date: day.entry_count for day in journal_data.days}
            self._selected_date = journal_data.selected_date
        else:
            self._dates = []
            self._entry_counts = {}
            self._selected_date = None
        self.call_later(self.recompose)

    def compose(self) -> ComposeResult:
        yield Static("Dates", classes="nav-header")
        
        if not self._dates:
            yield Static("No journal entries", classes="empty-state")
            return

        for date in self._dates:
            is_today = (datetime.now().date() == date.date())
            is_selected = self._selected_date and self._selected_date.date() == date.date()
            
            date_str = date.strftime("%a %m/%d")
            if is_today:
                date_str += " (Today)"
            elif (datetime.now().date() - date.date()).days == 1:
                date_str += " (Yesterday)"
            
            entry_count = self._entry_counts.get(date, 0)
            
            with Horizontal(classes="date-item"):
                label = Label(date_str, classes="date-label")
                if is_today:
                    label.add_class("date-today")
                if is_selected:
                    label.add_class("selected")
                yield label
                if entry_count > 0:
                    yield Label(str(entry_count), classes="date-count")

    def select_date(self, date: datetime):
        """Programmatically select a date."""
        self._selected_date = date
        self.call_later(self.recompose)