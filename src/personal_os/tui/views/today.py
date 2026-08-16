"""TodayView — the first composed workspace.

Demonstrates the composition hierarchy:

    Workspace (TodayView)
      -> Layout   (dashboard grid)
        -> Panels (labelled regions)
          -> Components (reusable: DashboardPanel, CapturesPanel, ...)

The router still only switches workspaces; all composition happens here, inside
the workspace, using reusable components bound to this workspace's
WorkspaceContext.

Milestone 5: Dashboard Backend Integration — components receive data from
TodayDashboardProvider instead of loading data themselves.
"""
from __future__ import annotations

from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from personal_os.tui.views.base import WorkspaceView
from personal_os.tui.components import (
    DashboardPanel,
    CapturesPanel,
    FocusPanel,
    UpcomingPanel,
    NotesPanel,
    TasksPanel,
)
from personal_os.tui.today_provider import create_today_provider


class _GreetingBanner(Static):
    """Top-of-day greeting banner — purple accent, matches design reference."""

    DEFAULT_CSS = """
    _GreetingBanner {
        border: round #a78bfa;
        background: $surface;
        padding: 1 2;
        margin: 0 0 1 0;
        height: auto;
        color: $text;
    }
    _GreetingBanner .greeting-title {
        color: #a78bfa;
        text-style: bold;
    }
    _GreetingBanner .greeting-date {
        color: $text-muted;
    }
    """

    def _greeting(self) -> str:
        hour = datetime.now().hour
        if hour < 12:
            return "Good morning"
        if hour < 18:
            return "Good afternoon"
        return "Good evening"

    def render(self) -> str:
        today = datetime.now().strftime("%A, %d %B %Y")
        return f"[bold #a78bfa]{self._greeting()}![/]\n[dim]{today}[/]"


# Accent colors used to color-code cards, matching the design reference —
# each workspace section gets a distinct, recognizable accent.
_ACCENTS = {
    "cyan": "#7dd3fc",
    "purple": "#a78bfa",
    "orange": "#fb923c",
    "green": "#4ade80",
    "blue": "#60a5fa",
    "pink": "#f472b6",
}


class _Card(Vertical):
    """A bordered card container for a dashboard panel, color-coded by accent."""

    DEFAULT_CSS = """
    _Card {
        border: round $border;
        margin: 0 1 1 0;
        height: 1fr;
        min-height: 12;
        background: $surface;
    }
    _Card:last-child {
        margin-right: 0;
    }
    _Card .card-header {
        text-style: bold;
        padding: 0 1;
    }
    _Card .card-content {
        padding: 0 1 1 1;
        height: 1fr;
    }
    """

    def __init__(self, title: str, *children, accent: str = "cyan", icon: str = "▸", **kwargs):
        kwargs.setdefault("classes", "card")
        super().__init__(**kwargs)
        self._title = title
        self._children_widgets = children
        self._accent = _ACCENTS.get(accent, _ACCENTS["cyan"])
        self._icon = icon

    def on_mount(self) -> None:
        self.styles.border = ("round", self._accent)

    def compose(self):
        header = Static(f"{self._icon} {self._title}", classes="card-header")
        header.styles.color = self._accent
        yield header
        with Vertical(classes="card-content"):
            for child in self._children_widgets:
                yield child


class TodayView(WorkspaceView):
    """Composed home workspace — functional dashboard with two-column grid."""

    view_id = "today"
    title = "Today"

    def __init__(self, services, **kwargs):
        super().__init__(services, **kwargs)
        # Create the data provider
        self._provider = create_today_provider(services)

    def on_activate(self):
        """Load fresh data when view becomes active."""
        super().on_activate()
        self._load_data()

    def _load_data(self):
        """Load fresh data from provider."""
        self._dashboard_data = self._provider.get_data(force_refresh=True)

    async def refresh_content(self):
        """Refresh all dashboard data and components."""
        self._load_data()
        await self._update_components()

    async def _update_components(self):
        """Update all child components with fresh data."""
        if not hasattr(self, '_dashboard_data') or not self._dashboard_data:
            return
        
        data = self._dashboard_data
        
        # Update DashboardPanel
        dashboard = self.query_one(DashboardPanel)
        if dashboard:
            metrics = [
                ("Projects", str(data.projects_count)),
                ("Active Tasks", str(data.active_tasks_count)),
                ("Captures Today", str(data.captures_today)),
                ("Hours Logged", f"{data.hours_logged:.1f}h"),
            ]
            current_project = None
            if data.current_project:
                current_project = {
                    "name": data.current_project.name,
                    "description": data.current_project.description,
                    "status": data.current_project.status,
                    "progress": data.current_project.progress or "████████░░ 80%",
                    "next_task": data.current_project.next_task or "No next task",
                    "milestone": data.current_project.milestone or "v0.2: Context & Scheduler",
                }
            dashboard.update_data(metrics, current_project)
        
        # Update CapturesPanel - convert CaptureEntry to tuple
        captures_panel = self.query_one(CapturesPanel)
        if captures_panel:
            captures_tuples = [
                (c.time_str, c.source, c.status, c.preview)
                for c in data.recent_captures
            ]
            captures_panel.update_data(captures_tuples, data.captures_today)
        
        # Update FocusPanel - convert FocusItem to tuple
        focus_panel = self.query_one(FocusPanel)
        if focus_panel:
            focus_tuples = {}
            for section, items in data.focus_items.items():
                focus_tuples[section] = [
                    (item.done, item.title, item.project, item.high_priority)
                    for item in items
                ]
            focus_panel.update_data(focus_tuples)
        
        # Update UpcomingPanel - convert UpcomingItem to tuple
        upcoming_panel = self.query_one(UpcomingPanel)
        if upcoming_panel:
            upcoming_tuples = {}
            for section, items in data.upcoming_items.items():
                upcoming_tuples[section] = [
                    (item.date_str, item.title, item.project, item.status)
                    for item in items
                ]
            upcoming_panel.update_data(upcoming_tuples)
        
        # Update NotesPanel - convert NoteItem to tuple
        notes_panel = self.query_one(NotesPanel)
        if notes_panel:
            notes_tuples = [
                (item.title, item.path, item.modified)
                for item in data.recent_notes
            ]
            notes_panel.update_data(notes_tuples)
        
        # Update TasksPanel - convert TaskItem to tuple
        tasks_panel = self.query_one(TasksPanel)
        if tasks_panel:
            tasks_tuples = [
                (item.done, item.title, item.project)
                for item in data.active_tasks
            ]
            tasks_panel.update_data(tasks_tuples)

    def compose(self) -> ComposeResult:
        # Ensure we have initial data
        if not hasattr(self, '_dashboard_data') or self._dashboard_data is None:
            self._load_data()
        
        yield _GreetingBanner()

        # TOP ROW - Primary: Current Project (wide) | Secondary: Recent Captures (narrow)
        with Horizontal():
            project_ctx = self.context.child(
                breadcrumbs=["Today", "Current Project"],
                mode="explore",
            )
            yield _Card("Current Project", DashboardPanel(context=project_ctx), accent="purple", icon="⬢")

            captures_ctx = self.context.child(
                breadcrumbs=["Today", "Recent Captures"],
                mode="explore",
            )
            yield _Card("Recent Captures", CapturesPanel(context=captures_ctx), accent="cyan", icon="◈")

        # MIDDLE ROW - Primary: Today's Focus (wide) | Secondary: Upcoming (narrow)
        with Horizontal():
            focus_ctx = self.context.child(
                breadcrumbs=["Today", "Today's Focus"],
                mode="explore",
            )
            yield _Card("Today's Focus", FocusPanel(context=focus_ctx), accent="green", icon="◎")

            upcoming_ctx = self.context.child(
                breadcrumbs=["Today", "Upcoming"],
                mode="explore",
            )
            yield _Card("Upcoming", UpcomingPanel(context=upcoming_ctx), accent="orange", icon="📁")

        # BOTTOM ROW - Primary: Recent Notes (wide) | Secondary: Active Tasks (narrow)
        with Horizontal():
            notes_ctx = self.context.child(
                breadcrumbs=["Today", "Recent Notes"],
                mode="explore",
            )
            yield _Card("Recent Notes", NotesPanel(context=notes_ctx), accent="blue", icon="📄")

            tasks_ctx = self.context.child(
                breadcrumbs=["Today", "Active Tasks"],
                mode="explore",
            )
            yield _Card("Active Tasks", TasksPanel(context=tasks_ctx), accent="pink", icon="☰")