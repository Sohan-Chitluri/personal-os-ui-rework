"""DashboardPanel — reusable, embeddable dashboard component.

A functional dashboard with metric tiles, project status, and quick stats.
Designed for terminal-native readability inspired by btop, Lazygit, and Yazi.

This component renders ONLY — all data comes from TodayDashboardProvider.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static, Label

from personal_os.tui.components.base import Component


class MetricTile(Static):
    """A single metric tile showing label and value."""

    DEFAULT_CSS = """
    MetricTile {
        border: round $border;
        padding: 1;
        margin: 0 1 0 0;
        height: 5;
        min-width: 18;
        background: $panel;
    }
    MetricTile .metric-label {
        color: $text-muted;
        text-style: bold;
    }
    MetricTile .metric-value {
        color: $primary;
        text-style: bold;
        content-align: center middle;
    }
    MetricTile:hover {
        border: round $accent;
    }
    """

    def __init__(self, label: str, value: str, **kwargs):
        super().__init__(**kwargs)
        self.label = label
        self.value = value

    def compose(self) -> ComposeResult:
        yield Label(self.label, classes="metric-label")
        yield Label(self.value, classes="metric-value")


class DashboardPanel(Component):
    """An embeddable dashboard surface with metric tiles and sections."""

    component_id = "dashboard-panel"
    title = "Dashboard"

    DEFAULT_CSS = """
    DashboardPanel {
        layout: vertical;
    }
    DashboardPanel .section-title {
        color: $primary;
        text-style: bold;
        padding: 1 0;
        margin-top: 1;
    }
    DashboardPanel .metrics-row {
        layout: horizontal;
        height: 7;
    }
    DashboardPanel .project-card {
        border: round $border;
        background: $panel;
        padding: 1;
        margin: 1 0;
        min-height: 8;
    }
    DashboardPanel .project-header {
        color: $accent;
        text-style: bold;
    }
    DashboardPanel .project-meta {
        color: $text-muted;
    }
    DashboardPanel .project-progress {
        color: $success;
        margin-top: 1;
    }
    DashboardPanel .project-milestone {
        color: $warning;
        margin-top: 1;
        text-style: bold;
    }
    """

    def __init__(self, context=None, **kwargs):
        super().__init__(context=context, **kwargs)
        # Data will be provided by TodayDashboardProvider
        self._metrics = [
            ("Projects", "0"),
            ("Active Tasks", "0"),
            ("Captures Today", "0"),
            ("Hours Logged", "0.0h"),
        ]
        self._current_project = None

    def update_data(self, metrics: list, current_project: dict = None):
        """Update dashboard with new data from provider."""
        self._metrics = metrics
        self._current_project = current_project
        self.call_later(self.recompose)

    def compose(self) -> ComposeResult:
        # Metrics row
        with Horizontal(classes="metrics-row"):
            for label, value in self._metrics:
                yield MetricTile(label, value)

        # Current project card
        yield Static("Current Project", classes="section-title")
        with Vertical(classes="project-card"):
            if self._current_project:
                yield Label(f"⬢ {self._current_project['name']}", classes="project-header")
                yield Label(self._current_project.get('description', ''), classes="project-meta")
                yield Label(f"Status: {self._current_project.get('status', 'active')}", classes="project-meta")
                progress = self._current_project.get('progress', '████████░░ 80%')
                yield Label(progress, classes="project-progress")
                yield Label(f"Next: {self._current_project.get('next_task', 'No next task')}", classes="project-meta")
                yield Label(f"Milestone: {self._current_project.get('milestone', 'v0.2: Context & Scheduler')}", classes="project-milestone")
            else:
                yield Label("No active project", classes="project-meta")
                yield Label("Create one with: pos new-project", classes="project-meta")

        # Quick actions hint
        yield Static("", classes="section-title")
        yield Label("1-9: workspace  |  Ctrl+I: capture  |  Ctrl+B: sidebar  |  j/k: navigate", classes="project-meta")