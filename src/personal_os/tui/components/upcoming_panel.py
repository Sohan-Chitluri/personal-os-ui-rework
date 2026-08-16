"""UpcomingPanel — upcoming deadlines and important dates panel.

Shows upcoming deadlines, scheduled items, and time-sensitive tasks.

This component renders ONLY — all data comes from TodayDashboardProvider.
"""
from __future__ import annotations

from typing import Any, Dict, List

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static, Label

from personal_os.tui.components.base import Component
from personal_os.tui.components.tag_colors import color_for


class UpcomingItem(Static):
    """A single upcoming deadline/item."""

    DEFAULT_CSS = """
    UpcomingItem {
        layout: horizontal;
        height: 1;
        padding: 0 1;
        background: transparent;
    }
    UpcomingItem:hover {
        background: $boost;
    }
    UpcomingItem .upcoming-date {
        color: $warning;
        width: 10;
        text-align: right;
        padding-right: 1;
    }
    UpcomingItem .upcoming-title {
        color: $text;
        width: 1fr;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    UpcomingItem .upcoming-project {
        color: $text-muted;
        width: 14;
        text-align: right;
    }
    UpcomingItem.overdue .upcoming-date {
        color: $error;
        text-style: bold;
    }
    UpcomingItem.today .upcoming-date {
        color: $accent;
        text-style: bold;
    }
    """

    def __init__(self, date_str: str, title: str, project: str, status: str = "", **kwargs):
        super().__init__(**kwargs)
        self.date_str = date_str
        self.title = title
        self.project = project
        self.status = status
        if status == "overdue":
            self.add_class("overdue")
        elif status == "today":
            self.add_class("today")

    def compose(self) -> ComposeResult:
        yield Label(self.date_str, classes="upcoming-date")
        yield Label(self.title, classes="upcoming-title")
        badge = Label(f" [{self.project}]", classes="upcoming-project")
        badge.styles.color = color_for(self.project)
        yield badge


class UpcomingPanel(Component):
    """An embeddable upcoming deadlines panel."""

    component_id = "upcoming-panel"
    title = "Upcoming"

    DEFAULT_CSS = """
    UpcomingPanel {
        layout: vertical;
    }
    UpcomingPanel .upcoming-header {
        color: $primary;
        text-style: bold;
        padding: 1 0;
    }
    UpcomingPanel .upcoming-list {
        height: 1fr;
        min-height: 12;
    }
    UpcomingPanel .upcoming-empty {
        color: $text-muted;
        text-align: center;
        padding: 2;
    }
    UpcomingPanel .upcoming-section {
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
        self.refresh()

    def compose(self) -> ComposeResult:
        yield Static("Upcoming", classes="upcoming-header")
        with Vertical(classes="upcoming-list"):
            if not self._sections:
                yield Static("No upcoming deadlines. Add dates to tasks in Tasks.md files.", classes="upcoming-empty")
            else:
                for section_name, items in self._sections.items():
                    yield Static(section_name, classes="upcoming-section")
                    for date_str, title, project, status in items:
                        yield UpcomingItem(date_str, title, project, status)