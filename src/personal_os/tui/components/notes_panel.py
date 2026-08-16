"""NotesPanel — reusable, embeddable recent notes component.

Shows recently modified Markdown documents with relative timestamps.

This component renders ONLY — all data comes from TodayDashboardProvider.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, List

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static, Label

from personal_os.tui.components.base import Component


class NoteItem(Static):
    """A single note item with title, path, and relative timestamp."""

    DEFAULT_CSS = """
    NoteItem {
        layout: horizontal;
        height: 1;
        padding: 0 1;
        background: transparent;
    }
    NoteItem:hover {
        background: $boost;
    }
    NoteItem .note-icon {
        color: $primary;
        width: 3;
        margin-right: 1;
    }
    NoteItem .note-title {
        color: $text;
        width: 1fr;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    NoteItem .note-path {
        color: $text-muted;
        width: 24;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    NoteItem .note-time {
        color: $text-muted;
        width: 10;
        text-align: right;
    }
    """

    def __init__(self, title: str, path: str, modified: datetime, **kwargs):
        super().__init__(**kwargs)
        self.title = title
        self.path = path
        self.modified = modified

    def _format_relative_time(self, dt: datetime) -> str:
        now = datetime.now()
        diff = now - dt
        if diff.days > 0:
            return f"{diff.days}d ago"
        hours = diff.seconds // 3600
        if hours > 0:
            return f"{hours}h ago"
        minutes = diff.seconds // 60
        if minutes > 0:
            return f"{minutes}m ago"
        return "just now"

    def compose(self) -> ComposeResult:
        rel_time = self._format_relative_time(self.modified)
        yield Label("📄", classes="note-icon")
        yield Label(self.title, classes="note-title")
        yield Label(self.path, classes="note-path")
        yield Label(rel_time, classes="note-time")


class NotesPanel(Component):
    """An embeddable recent notes panel."""

    component_id = "notes-panel"
    title = "Recent Notes"

    DEFAULT_CSS = """
    NotesPanel {
        layout: vertical;
    }
    NotesPanel .notes-header {
        color: $primary;
        text-style: bold;
        padding: 1 0;
    }
    NotesPanel .notes-list {
        height: 1fr;
        min-height: 12;
    }
    NotesPanel .notes-empty {
        color: $text-muted;
        text-align: center;
        padding: 2;
    }
    """

    def __init__(self, context=None, **kwargs):
        super().__init__(context=context, **kwargs)
        self._notes: List[tuple] = []

    def update_data(self, notes: List[tuple]):
        """Update panel with new data from provider."""
        self._notes = notes
        self.call_later(self.recompose)

    def compose(self) -> ComposeResult:
        yield Static("Recent Notes", classes="notes-header")
        with Vertical(classes="notes-list"):
            if not self._notes:
                yield Static("No recent notes. Notes appear here when you edit markdown files.", classes="notes-empty")
            else:
                for title, path, modified in self._notes:
                    yield NoteItem(title, path, modified)