"""Views package."""
from personal_os.tui.views.base import WorkspaceView
from personal_os.tui.views.today import TodayView
from personal_os.tui.views.tasks import TasksView
from personal_os.tui.views.projects import ProjectsView
from personal_os.tui.views.search import SearchView
from personal_os.tui.views.journal import JournalView
from personal_os.tui.views.placeholders import (
    VIEW_REGISTRY,
    KnowledgeView,
    ReviewsView, FavoritesView, SettingsView,
)

__all__ = [
    "WorkspaceView", "VIEW_REGISTRY",
    "TodayView", "ProjectsView", "TasksView", "JournalView", "KnowledgeView",
    "SearchView", "ReviewsView", "FavoritesView", "SettingsView",
]
