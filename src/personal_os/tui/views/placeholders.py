"""Placeholder workspace views for the Milestone 2 framework.

Each view is a distinct WorkspaceView subclass so later milestones can flesh
them out (dashboards, markdown, search) without touching the router or the
navigation state. Intentionally no real content here.
"""
from __future__ import annotations

from personal_os.tui.views.base import WorkspaceView
from personal_os.tui.views.today import TodayView
from personal_os.tui.views.tasks import TasksView  # real implementation — W1
from personal_os.tui.views.projects import ProjectsView
from personal_os.tui.views.journal import JournalView
from personal_os.tui.views.search import SearchView


class KnowledgeView(WorkspaceView):
    view_id = "knowledge"
    title = "Knowledge"

    def compose(self):
        from textual.app import ComposeResult
        from textual.widgets import Static
        yield Static(
            f"[bold #a78bfa]{self.title}[/]\n\n"
            f"[dim]Learned concepts, skills progress, and study notes[/]\n\n"
            f"[dim]· Planned for Milestone 2[/]",
            classes="workspace-placeholder"
        )


class ReviewsView(WorkspaceView):
    view_id = "reviews"
    title = "Reviews"

    def compose(self):
        from textual.app import ComposeResult
        from textual.widgets import Static
        yield Static(
            f"[bold #a78bfa]{self.title}[/]\n\n"
            f"[dim]Weekly and monthly reviews, retrospectives[/]\n\n"
            f"[dim]· Planned for Milestone 2[/]",
            classes="workspace-placeholder"
        )


class FavoritesView(WorkspaceView):
    view_id = "favorites"
    title = "Favorites"

    def compose(self):
        from textual.app import ComposeResult
        from textual.widgets import Static
        yield Static(
            f"[bold #a78bfa]{self.title}[/]\n\n"
            f"[dim]Pinned notes, bookmarked captures, quick-access items[/]\n\n"
            f"[dim]· Planned for Milestone 2[/]",
            classes="workspace-placeholder"
        )


class SettingsView(WorkspaceView):
    view_id = "settings"
    title = "Settings"

    def compose(self):
        from textual.app import ComposeResult
        from textual.widgets import Static
        yield Static(
            f"[bold #a78bfa]{self.title}[/]\n\n"
            f"[dim]Configuration, vault path, model settings, keybindings[/]\n\n"
            f"[dim]· Planned for Milestone 2[/]",
            classes="workspace-placeholder"
        )


# Registry: view_id -> class. The router builds instances from this.
VIEW_REGISTRY = {
    v.view_id: v
    for v in (
        TodayView, ProjectsView, TasksView, JournalView, KnowledgeView,
        SearchView, ReviewsView, FavoritesView, SettingsView,
    )
}
