"""TasksView — full-workspace task list grouped by project.

Shows all open and completed tasks across the entire vault, organized by
project with pending tasks first. This is a read-only M1 view — task
mutation comes in a later milestone once KnowledgeApplier is wired.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, ScrollableContainer
from textual.widgets import Static

from personal_os.tui.views.base import WorkspaceView
from personal_os.tui.tasks_provider import AllTasksData, ProjectTaskGroup, create_tasks_provider

_PENDING_ICON = "○"
_DONE_ICON = "✓"
_EMPTY_ICON = "·"


class _SectionHeader(Static):
    DEFAULT_CSS = """
    _SectionHeader {
        color: #a78bfa;
        text-style: bold;
        padding: 1 2 0 2;
        height: auto;
    }
    """


class _TaskLine(Static):
    DEFAULT_CSS = """
    _TaskLine {
        padding: 0 2 0 4;
        height: auto;
    }
    _TaskLine.done {
        color: $text-muted;
        text-style: strike;
    }
    """

    def __init__(self, done: bool, title: str, **kwargs):
        icon = _DONE_ICON if done else _PENDING_ICON
        text = f"[dim]{icon}[/] {title}" if done else f"{icon} {title}"
        super().__init__(text, **kwargs)
        if done:
            self.add_class("done")


class _TasksHeader(Static):
    DEFAULT_CSS = """
    _TasksHeader {
        border: round #a78bfa;
        background: $surface;
        padding: 0 2;
        margin: 0 0 1 0;
        height: auto;
        color: $text;
    }
    """

    def __init__(self, data: AllTasksData, **kwargs):
        completed_hint = f"  [dim]{data.total_completed} completed[/]" if data.total_completed else ""
        text = (
            f"[bold #a78bfa]Tasks[/]  "
            f"[bold]{data.total_pending}[/] pending{completed_hint}"
        )
        super().__init__(text, **kwargs)


class _EmptyState(Static):
    DEFAULT_CSS = """
    _EmptyState {
        padding: 4 2;
        color: $text-muted;
        text-align: center;
        height: auto;
    }
    """

    def render(self) -> str:
        return (
            f"[dim]{_EMPTY_ICON} No tasks found[/]\n\n"
            "[dim]Capture a task with:[/]\n"
            "[dim]  pos note \"I need to ...\"\n"
            "  or use the capture bar in the TUI[/]"
        )


class _ProjectSection(Vertical):
    """One project's task block — header + pending items + completed items."""

    DEFAULT_CSS = """
    _ProjectSection {
        border-bottom: solid $border;
        height: auto;
        padding: 0 0 1 0;
    }
    """

    def __init__(self, group: ProjectTaskGroup, show_completed: bool = False, **kwargs):
        super().__init__(**kwargs)
        self._group = group
        self._show_completed = show_completed

    def compose(self) -> ComposeResult:
        g = self._group
        pending_txt = f"{g.pending_count} pending"
        completed_txt = f"  [dim]{g.completed_count} done[/]" if g.completed_count else ""
        yield _SectionHeader(f"[bold]{g.project}[/]  [dim]{pending_txt}{completed_txt}[/]")

        if not g.pending and not g.completed:
            yield Static(f"  [dim]{_EMPTY_ICON} all done[/]", classes="empty-project")
            return

        for item in g.pending:
            yield _TaskLine(done=False, title=item.title)

        if self._show_completed and g.completed:
            for item in g.completed[:5]:  # cap at 5 completed per project
                yield _TaskLine(done=True, title=item.title)


class TasksView(WorkspaceView):
    """Full-workspace task list — all vault tasks grouped by project."""

    view_id = "tasks"
    title = "Tasks"

    DEFAULT_CSS = """
    TasksView {
        padding: 0;
    }
    """

    def __init__(self, services, **kwargs):
        super().__init__(services, **kwargs)
        self._provider = create_tasks_provider(services)
        self._data: AllTasksData | None = None

    def on_activate(self) -> None:
        super().on_activate()
        self._refresh_data()

    async def refresh_content(self) -> None:
        self._refresh_data()
        await self._rebuild()

    def _refresh_data(self) -> None:
        self._data = self._provider.get_data(force_refresh=True)

    def compose(self) -> ComposeResult:
        if self._data is None:
            self._refresh_data()

        data = self._data
        yield _TasksHeader(data)

        if not data.groups:
            yield _EmptyState()
            return

        with ScrollableContainer():
            for group in data.groups:
                yield _ProjectSection(group, show_completed=False)

    async def _rebuild(self) -> None:
        """Remount the scrollable content region with fresh data."""
        try:
            container = self.query_one(ScrollableContainer)
            await container.remove()
        except Exception:
            pass

        if self._data and self._data.groups:
            container = ScrollableContainer()
            await self.mount(container)
            for group in self._data.groups:
                await container.mount(_ProjectSection(group, show_completed=False))
