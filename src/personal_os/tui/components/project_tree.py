"""ProjectTree — reusable, embeddable project tree component.

Shows a hierarchical list of projects with status/health indicators.
Supports project selection for detail view.
Terminal-native design with color-coded status and health indicators.

This component renders ONLY — all data comes from ProjectsProvider.
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional

from textual.app import ComposeResult
from textual.containers import Vertical, ScrollableContainer, Horizontal
from textual.widgets import Static, Label

from personal_os.tui.components.base import Component
from personal_os.tui.providers.projects_provider import ProjectsData, ProjectDetail
from personal_os.tui.components.tag_colors import color_for


# Health/Status color mapping
HEALTH_COLORS = {
    "green": "#4ade80",
    "yellow": "#fbbf24",
    "red": "#f87171",
}

STATUS_COLORS = {
    "active": "#60a5fa",
    "planning": "#a78bfa",
    "blocked": "#f87171",
    "on-hold": "#fbbf24",
    "completed": "#4ade80",
    "archived": "#9ca3af",
}

HEALTH_ICONS = {
    "green": "●",
    "yellow": "◐",
    "red": "●",
}

STATUS_ICONS = {
    "active": "▸",
    "planning": "○",
    "blocked": "✖",
    "on-hold": "⏸",
    "completed": "✓",
    "archived": "⌂",
}


class ProjectTreeNode(Static):
    """A single project node in the tree."""

    DEFAULT_CSS = """
    ProjectTreeNode {
        layout: horizontal;
        height: 1;
        padding: 0 1;
        background: transparent;
    }
    ProjectTreeNode:hover {
        background: $boost;
    }
    ProjectTreeNode.selected {
        background: $accent 30%;
    }
    ProjectTreeNode .health-indicator {
        width: 2;
        text-align: center;
    }
    ProjectTreeNode .status-indicator {
        width: 2;
        text-align: center;
    }
    ProjectTreeNode .project-name {
        width: 1fr;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    ProjectTreeNode .project-meta {
        color: $text-muted;
        width: 24;
        text-align: right;
    }
    ProjectTreeNode .open-tasks {
        color: #fbbf24;
        width: 8;
        text-align: right;
    }
    """

    def __init__(
        self,
        project: ProjectDetail,
        is_selected: bool = False,
        callback: Optional[Callable[[str], None]] = None,
        **kwargs
    ):
        kwargs.setdefault("classes", "project-node")
        super().__init__(**kwargs)
        self.project = project
        self.is_selected = is_selected
        self.callback = callback

        if is_selected:
            self.add_class("selected")

    def compose(self) -> ComposeResult:
        # Health indicator
        health_color = HEALTH_COLORS.get(self.project.health, HEALTH_COLORS["green"])
        health_icon = HEALTH_ICONS.get(self.project.health, "●")
        health = Label(health_icon, classes="health-indicator")
        health.styles.color = health_color

        # Status indicator
        status_color = STATUS_COLORS.get(self.project.status, STATUS_COLORS["active"])
        status_icon = STATUS_ICONS.get(self.project.status, "▸")
        status = Label(status_icon, classes="status-indicator")
        status.styles.color = status_color

        # Project name
        name = Label(self.project.name, classes="project-name")

        # Open tasks count
        tasks_text = f"{self.project.open_tasks}" if self.project.open_tasks > 0 else "—"
        tasks = Label(tasks_text, classes="open-tasks")

        # Last activity
        activity = ""
        if self.project.last_activity:
            activity = self.project.last_activity
        meta = Label(activity, classes="project-meta")

        yield health
        yield status
        yield name
        yield tasks
        yield meta

    def on_click(self, event) -> None:
        """Handle click to select project."""
        if self.callback:
            self.callback(self.project.name)


class ProjectTree(Component):
    """An embeddable project tree panel with selection support."""

    component_id = "project-tree"
    title = "Projects"

    DEFAULT_CSS = """
    ProjectTree {
        layout: vertical;
    }
    ProjectTree .tree-header {
        color: $primary;
        text-style: bold;
        padding: 1 0;
    }
    ProjectTree .tree-header-row {
        layout: horizontal;
        height: 1;
        padding: 0 1;
        color: $text-muted;
        text-style: bold;
    }
    ProjectTree .tree-header-row .col {
        width: 1fr;
    }
    ProjectTree .tree-header-row .col-health {
        width: 2;
    }
    ProjectTree .tree-header-row .col-status {
        width: 2;
    }
    ProjectTree .col-tasks {
        width: 8;
    }
    ProjectTree .col-meta {
        width: 24;
    }
    ProjectTree .tree-container {
        height: 1fr;
        min-height: 10;
        border: round $border;
        background: $surface;
    }
    ProjectTree .empty-state {
        color: $text-muted;
        text-align: center;
        padding: 4;
    }
    ProjectTree .legend {
        color: $text-muted;
        padding: 1;
    }
    """

    def __init__(
        self,
        context=None,
        on_select: Optional[Callable[[str], None]] = None,
        **kwargs
    ):
        super().__init__(context=context, **kwargs)
        self._projects: List[ProjectDetail] = []
        self._selected_project: Optional[str] = None
        self.on_select = on_select

    def update_data(self, projects_data: ProjectsData):
        """Update tree with new data from provider."""
        self._projects = projects_data.projects
        self._selected_project = projects_data.selected_project
        self.call_later(self.recompose)

    def select_project(self, project_name: str) -> bool:
        """Select a project programmatically."""
        for p in self._projects:
            if p.name == project_name:
                self._selected_project = project_name
                self.call_later(self.recompose)
                return True
        return False

    def get_selected(self) -> Optional[ProjectDetail]:
        """Get the currently selected project detail."""
        if self._selected_project:
            for p in self._projects:
                if p.name == self._selected_project:
                    return p
        return None

    def compose(self) -> ComposeResult:
        # Header row
        yield Static("Projects", classes="tree-header")

        # Column headers
        with Vertical(classes="tree-header-row"):
            with Horizontal():
                yield Label("●", classes="col col-health")
                yield Label("▸", classes="col col-status")
                yield Label("Name", classes="col")
                yield Label("Tasks", classes="col col-tasks")
                yield Label("Last Activity", classes="col col-meta")

        # Tree container
        with ScrollableContainer(classes="tree-container"):
            if not self._projects:
                yield Static("No projects found.\nCreate one with: pos new-project", classes="empty-state")
            else:
                for project in self._projects:
                    is_selected = project.name == self._selected_project
                    node = ProjectTreeNode(
                        project,
                        is_selected=is_selected,
                        callback=self._handle_select
                    )
                    yield node

        # Legend
        yield Static(
            "● Health: [#4ade80]green[/] [#fbbf24]yellow[/] [#f87171]red[/]  |  "
            "▸ Status: [#60a5fa]active[/] [#a78bfa]planning[/] [#f87171]blocked[/] [#fbbf24]on-hold[/] [#4ade80]done[/]",
            classes="legend"
        )

    def _handle_select(self, project_name: str):
        """Handle project selection from node click."""
        self._selected_project = project_name
        self.call_later(self.recompose)
        if self.on_select:
            self.on_select(project_name)