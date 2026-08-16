"""CapturesPanel — reusable, embeddable recent captures component.

Shows recent capture entries from the daily stream with processing status.

This component renders ONLY — all data comes from TodayDashboardProvider.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, List, Tuple

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static, Label

from personal_os.tui.components.base import Component
from personal_os.tui.components.tag_colors import color_for


class CaptureRow(Static):
    """A single capture row showing timestamp, source, status, and preview."""

    DEFAULT_CSS = """
    CaptureRow {
        layout: horizontal;
        height: 1;
        padding: 0 1;
        background: transparent;
    }
    CaptureRow:hover {
        background: $boost;
    }
    CaptureRow .capture-time {
        color: $text-muted;
        width: 8;
        text-align: right;
    }
    CaptureRow .capture-source {
        color: $primary;
        width: 8;
        text-style: bold;
    }
    CaptureRow .capture-status {
        color: $success;
        width: 12;
    }
    CaptureRow .capture-status.pending {
        color: $warning;
    }
    CaptureRow .capture-status.error {
        color: $error;
    }
    CaptureRow .capture-preview {
        color: $text;
        width: 1fr;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    """

    def __init__(self, time_str: str, source: str, status: str, preview: str, **kwargs):
        super().__init__(**kwargs)
        self.time_str = time_str
        self.source = source
        self.status = status
        self.preview = preview

    def compose(self) -> ComposeResult:
        status_class = "pending" if self.status == "pending" else ("error" if self.status == "error" else "")
        icon = "🎤" if self.source.lower() == "voice" else "T"
        yield Label(self.time_str, classes="capture-time")
        source_label = Label(icon, classes="capture-source")
        source_label.styles.color = color_for(self.source)
        yield source_label
        yield Label(self.status, classes=f"capture-status {status_class}")
        yield Label(self.preview, classes="capture-preview")


class CapturesPanel(Component):
    """An embeddable recent captures panel."""

    component_id = "captures-panel"
    title = "Recent Captures"

    DEFAULT_CSS = """
    CapturesPanel {
        layout: vertical;
    }
    CapturesPanel .captures-header {
        color: $primary;
        text-style: bold;
        padding: 1 0;
    }
    CapturesPanel .captures-list {
        height: 1fr;
        min-height: 12;
    }
    CapturesPanel .captures-empty {
        color: $text-muted;
        text-align: center;
        padding: 2;
    }
    """

    def __init__(self, context=None, **kwargs):
        super().__init__(context=context, **kwargs)
        self._captures: List[Tuple[str, str, str, str]] = []
        self._captures_today = 0

    def update_data(self, captures: List[Tuple[str, str, str, str]], captures_today: int = 0):
        """Update panel with new data from provider."""
        self._captures = captures
        self._captures_today = captures_today
        self.call_later(self.recompose)

    def compose(self) -> ComposeResult:
        yield Static("Recent Captures", classes="captures-header")
        with Vertical(classes="captures-list"):
            if not self._captures:
                yield Static("No recent captures. Press Ctrl+I to capture something.", classes="captures-empty")
            else:
                for time_str, source, status, preview in self._captures:
                    yield CaptureRow(time_str, source, status, preview)