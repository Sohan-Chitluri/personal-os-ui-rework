"""Reusable UI components.

Components are the leaves of the Workspace -> Layout -> Panels -> Components
hierarchy. They are NOT workspaces and are never reachable from the navigator.
They are embeddable inside any workspace and depend only on a WorkspaceContext.
"""
from personal_os.tui.components.base import Component
from personal_os.tui.components.dashboard_panel import DashboardPanel
from personal_os.tui.components.tasks_panel import TasksPanel
from personal_os.tui.components.captures_panel import CapturesPanel
from personal_os.tui.components.notes_panel import NotesPanel
from personal_os.tui.components.focus_panel import FocusPanel
from personal_os.tui.components.upcoming_panel import UpcomingPanel
from personal_os.tui.components.project_tree import ProjectTree
from personal_os.tui.components.search_results import SearchResults
from personal_os.tui.components.journal_entry import JournalList, JournalDateNavigator

__all__ = [
    "Component",
    "DashboardPanel",
    "TasksPanel",
    "CapturesPanel",
    "NotesPanel",
    "FocusPanel",
    "UpcomingPanel",
    "ProjectTree",
    "SearchResults",
    "JournalList",
    "JournalDateNavigator",
]