"""Panel widgets package."""
from personal_os.tui.widgets.panels.projects import ProjectsPanel
from personal_os.tui.widgets.panels.tasks import TasksPanel
from personal_os.tui.widgets.panels.journal import JournalPanel
from personal_os.tui.widgets.panels.search import SearchPanel
from personal_os.tui.widgets.panels.calendar import CalendarPanel
from personal_os.tui.widgets.panels.settings import SettingsPanel
from personal_os.tui.widgets.panels.base import SidebarPanel

__all__ = [
    "ProjectsPanel",
    "TasksPanel", 
    "JournalPanel",
    "SearchPanel",
    "CalendarPanel",
    "SettingsPanel",
    "SidebarPanel",
]