"""Placeholder workspace views for the Milestone 2 framework.

Each view is a distinct WorkspaceView subclass so later milestones can flesh
them out (dashboards, markdown, search) without touching the router or the
navigation state. Intentionally no real content here.
"""
from __future__ import annotations

from personal_os.tui.views.base import WorkspaceView


class TodayView(WorkspaceView):
    view_id = "today"
    title = "Today"


class ProjectsView(WorkspaceView):
    view_id = "projects"
    title = "Projects"


class TasksView(WorkspaceView):
    view_id = "tasks"
    title = "Tasks"


class JournalView(WorkspaceView):
    view_id = "journal"
    title = "Journal"


class KnowledgeView(WorkspaceView):
    view_id = "knowledge"
    title = "Knowledge"


class SearchView(WorkspaceView):
    view_id = "search"
    title = "Search"


class ReviewsView(WorkspaceView):
    view_id = "reviews"
    title = "Reviews"


class FavoritesView(WorkspaceView):
    view_id = "favorites"
    title = "Favorites"


class SettingsView(WorkspaceView):
    view_id = "settings"
    title = "Settings"


# Registry: view_id -> class. The router builds instances from this.
VIEW_REGISTRY = {
    v.view_id: v
    for v in (
        TodayView, ProjectsView, TasksView, JournalView, KnowledgeView,
        SearchView, ReviewsView, FavoritesView, SettingsView,
    )
}
