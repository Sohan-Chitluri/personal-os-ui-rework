"""TasksPanel — reusable, embeddable tasks component.

Shows highest priority active tasks with project labels.
Terminal-native design with checkbox-style indicators.

This component renders ONLY — all data comes from TodayDashboardProvider.
"""
from __future__ import annotations

from typing import Any, List, Tuple

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static, Label

from personal_os.tui.components.base import Component
from personal_os.tui.components.tag_colors import color_for


class TaskRow(Static):
    """A single task row with checkbox, title, and project label."""

    DEFAULT_CSS = """
    TaskRow {
        layout: horizontal;
        height: 1;
        padding: 0 1;
        background: transparent;
    }
    TaskRow:hover {
        background: $boost;
    }
    TaskRow .task-checkbox {
        color: $primary;
        width: 4;
        text-align: center;
    }
    TaskRow .task-checkbox.done {
        color: $success;
    }
    TaskRow .task-title {
        color: $text;
        width: 1fr;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    TaskRow .task-project {
        color: $text-muted;
        width: 16;
        text-align: right;
    }
    """

    def __init__(self, done: bool, title: str, project: str, **kwargs):
        super().__init__(**kwargs)
        self.done = done
        self.title = title
        self.project = project

    def compose(self) -> ComposeResult:
        checkbox = "☑" if self.done else "☐"
        checkbox_class = "done" if self.done else ""
        yield Label(checkbox, classes=f"task-checkbox {checkbox_class}")
        yield Label(self.title, classes="task-title")
        badge = Label(f" {self.project} ", classes="task-project")
        badge.styles.color = color_for(self.project)
        yield badge


class TasksPanel(Component):
    """An embeddable active tasks panel."""

    component_id = "tasks-panel"
    title = "Active Tasks"

    DEFAULT_CSS = """
    TasksPanel {
        layout: vertical;
    }
    TasksPanel .tasks-header {
        color: $primary;
        text-style: bold;
        padding: 1 0;
    }
    TasksPanel .tasks-list {
        height: 1fr;
        min-height: 12;
    }
    TasksPanel .tasks-empty {
        color: $text-muted;
        text-align: center;
        padding: 2;
    }
    """

    def __init__(self, context=None, **kwargs):
        super().__init__(context=context, **kwargs)
        self._tasks: List[tuple] = []

    def update_data(self, tasks: List[Tuple[bool, str, str]]):
            """Update panel with new data from provider."""
            self._tasks = tasks
            self.call_later(self.recompose)

    def compose(self) -> ComposeResult:
        yield Static("Active Tasks", classes="tasks-header")
        with Vertical(classes="tasks-list"):
            if not self._tasks:
                yield Static("No active tasks. Create a project and add tasks to Tasks.md.", classes="tasks-empty")
            else:
                for done, title, project in self._tasks:
                    yield TaskRow(done, title, project)