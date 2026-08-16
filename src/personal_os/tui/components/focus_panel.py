"""FocusPanel — Today's focus / priorities panel.

Shows top priorities for the day with checkboxes.

This component renders ONLY — all data comes from TodayDashboardProvider.
"""
from __future__ import annotations

from typing import Any, Dict, List

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static, Label

from personal_os.tui.components.base import Component
from personal_os.tui.components.tag_colors import color_for


class FocusItem(Static):
    """A single focus item with checkbox and description."""

    DEFAULT_CSS = """
    FocusItem {
        layout: horizontal;
        height: 1;
        padding: 0 1;
        background: transparent;
    }
    FocusItem:hover {
        background: $boost;
    }
    FocusItem .focus-checkbox {
        color: $primary;
        width: 4;
        text-align: center;
    }
    FocusItem .focus-checkbox.done {
        color: $success;
    }
    FocusItem .focus-title {
        color: $text;
        width: 1fr;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    FocusItem .focus-tag {
        color: $text-muted;
        width: 14;
        text-align: right;
    }
    FocusItem.high-priority .focus-title {
        color: $accent;
        text-style: bold;
    }
    """

    def __init__(self, done: bool, title: str, tag: str, high_priority: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.done = done
        self.title = title
        self.tag = tag
        self.high_priority = high_priority

    def compose(self) -> ComposeResult:
        checkbox = "☑" if self.done else "☐"
        checkbox_class = "done" if self.done else ""
        if self.high_priority:
            self.add_class("high-priority")
        yield Label(checkbox, classes=f"focus-checkbox {checkbox_class}")
        yield Label(self.title, classes="focus-title")
        tag_label = Label(f" [{self.tag}]", classes="focus-tag")
        tag_label.styles.color = color_for(self.tag)
        yield tag_label


class FocusPanel(Component):
    """An embeddable today's focus panel."""

    component_id = "focus-panel"
    title = "Today's Focus"

    DEFAULT_CSS = """
    FocusPanel {
        layout: vertical;
    }
    FocusPanel .focus-header {
        color: $primary;
        text-style: bold;
        padding: 1 0;
    }
    FocusPanel .focus-list {
        height: 1fr;
        min-height: 12;
    }
    FocusPanel .focus-empty {
        color: $text-muted;
        text-align: center;
        padding: 2;
    }
    FocusPanel .focus-section {
        color: $accent;
        text-style: bold;
        padding: 1 1 0 1;
    }
    """

    def __init__(self, context=None, **kwargs):
        super().__init__(context=context, **kwargs)
        self._sections: Dict[str, List[tuple]] = {}

    def update_data(self, sections: Dict[str, List[tuple]]):
        """Update panel with new data from provider."""
        self._sections = sections
        self.call_later(self.recompose)

    def compose(self) -> ComposeResult:
        yield Static("Today's Focus", classes="focus-header")
        with Vertical(classes="focus-list"):
            if not self._sections:
                yield Static("No focus items. Create a task with Ctrl+I or add one in Projects.", classes="focus-empty")
            else:
                for section_name, items in self._sections.items():
                    yield Static(section_name, classes="focus-section")
                    for done, title, tag, high_priority in items:
                        yield FocusItem(done, title, tag, high_priority)