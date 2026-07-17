"""Views package."""
from personal_os.tui.views.base import WorkspaceView
from personal_os.tui.views.placeholders import (
    VIEW_REGISTRY,
    TodayView, ProjectsView, TasksView, JournalView, KnowledgeView,
    SearchView, ReviewsView, FavoritesView, SettingsView,
)

__all__ = [
    "WorkspaceView", "VIEW_REGISTRY",
    "TodayView", "ProjectsView", "TasksView", "JournalView", "KnowledgeView",
    "SearchView", "ReviewsView", "FavoritesView", "SettingsView",
]
