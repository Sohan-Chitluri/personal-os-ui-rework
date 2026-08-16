"""ProjectsView — the Projects workspace.

Demonstrates the composition hierarchy:

    Workspace (ProjectsView)
      -> Layout   (two-column: tree | detail)
        -> Panels (labelled regions)
          -> Components (reusable: ProjectTree, MarkdownDocumentPanel)

The router still only switches workspaces; all composition happens here,
inside the workspace, using reusable components bound to this workspace's
WorkspaceContext.

Milestone 2: Projects workspace with project listing/tree, selection,
and detail view with access to project's Markdown documents.
"""
from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.widgets import Static, Label, TabbedContent, TabPane, Button
from textual.message import Message

from personal_os.tui.views.base import WorkspaceView
from personal_os.tui.components import ProjectTree
from personal_os.tui.providers.projects_provider import (
    ProjectsProvider,
    ProjectsData,
    ProjectDetail,
    create_projects_provider,
)


class ProjectSelected(Message):
    """Message sent when a project is selected."""
    def __init__(self, project_name: str) -> None:
        self.project_name = project_name
        super().__init__()


class MarkdownDocumentPanel(Vertical):
    """A panel for displaying a single markdown document."""

    DEFAULT_CSS = """
    MarkdownDocumentPanel {
        layout: vertical;
        height: 1fr;
        border: round $border;
        background: $surface;
    }
    MarkdownDocumentPanel .doc-header {
        color: $primary;
        text-style: bold;
        padding: 1;
        border-bottom: solid $border;
    }
    MarkdownDocumentPanel .doc-content {
        padding: 1;
        height: 1fr;
        overflow-y: auto;
    }
    MarkdownDocumentPanel .doc-empty {
        color: $text-muted;
        text-align: center;
        padding: 4;
    }
    """

    def __init__(self, title: str, content: str = "", **kwargs):
        kwargs.setdefault("classes", "markdown-document-panel")
        super().__init__(**kwargs)
        self._title = title
        self._content = content

    def update_content(self, content: str):
        """Update the document content."""
        self._content = content
        self.call_later(self.recompose)

    def compose(self) -> ComposeResult:
        yield Static(self._title, classes="doc-header")
        with ScrollableContainer(classes="doc-content"):
            if not self._content.strip():
                yield Static("(empty)", classes="doc-empty")
            else:
                # Render markdown content as plain text with basic formatting
                yield Static(self._content, classes="doc-text")


class ProjectDetailPanel(Vertical):
    """Panel showing project detail with tabbed documents."""

    DEFAULT_CSS = """
    ProjectDetailPanel {
        layout: vertical;
        height: 1fr;
        border: round $border;
        background: $surface;
    }
    ProjectDetailPanel .detail-header {
        padding: 1;
        border-bottom: solid $border;
        background: $panel;
    }
    ProjectDetailPanel .detail-title {
        color: $accent;
        text-style: bold;
    }
    ProjectDetailPanel .detail-meta {
        color: $text-muted;
    }
    ProjectDetailPanel .detail-progress {
        color: $success;
        margin-top: 1;
    }
    ProjectDetailPanel .detail-milestone {
        color: $warning;
        margin-top: 1;
        text-style: bold;
    }
    ProjectDetailPanel .detail-next-task {
        color: #fbbf24;
        margin-top: 1;
    }
    ProjectDetailPanel .big-tasks-section {
        padding: 1;
        border-bottom: solid $border;
    }
    ProjectDetailPanel .big-task-item {
        margin: 1 0;
        padding: 1;
        border: round $border;
        background: $panel;
    }
    ProjectDetailPanel .big-task-title {
        color: $primary;
        text-style: bold;
    }
    ProjectDetailPanel .big-task-progress {
        color: $success;
    }
    ProjectDetailPanel .big-task-status {
        color: $text-muted;
    }
    ProjectDetailPanel .big-task-subtask {
        padding-left: 2;
    }
    ProjectDetailPanel .big-task-subtask.done {
        color: $text-muted;
        text-style: strike;
    }
    """

    def __init__(self, project: Optional[ProjectDetail] = None, **kwargs):
        kwargs.setdefault("classes", "project-detail-panel")
        super().__init__(**kwargs)
        self._project = project
        self._doc_panels = {}

    def update_project(self, project: ProjectDetail):
        """Update with new project data."""
        self._project = project
        self.call_later(self.recompose)

    def compose(self) -> ComposeResult:
        if not self._project:
            yield Static("Select a project to view details", classes="detail-empty")
            return

        p = self._project

        # Header with project info
        with Vertical(classes="detail-header"):
            yield Static(f"⬢ {p.name}", classes="detail-title")
            if p.description:
                yield Static(p.description, classes="detail-meta")
            yield Static(f"Status: {p.status}  |  Health: {p.health}  |  Tasks: {p.open_tasks}  |  Hours: {p.total_hours:.1f}h", classes="detail-meta")
            if p.progress:
                yield Static(p.progress, classes="detail-progress")
            if p.milestone:
                yield Static(f"Milestone: {p.milestone}", classes="detail-milestone")
            if p.next_task:
                yield Static(f"Next: {p.next_task}", classes="detail-next-task")

        # Big Tasks section
        if p.big_tasks:
            yield Static("Big Tasks", classes="detail-meta")
            with Vertical(classes="big-tasks-section"):
                for bt in p.big_tasks:
                    with Vertical(classes="big-task-item"):
                        yield Static(f"■ {bt.title} ({bt.project})", classes="big-task-title")
                        if bt.description:
                            yield Static(bt.description, classes="detail-meta")
                        yield Static(f"Progress: {bt.progress:.0%} ({bt.completed}/{bt.total})  |  Status: {bt.status}", classes="big-task-progress")
                        for subtask in bt.subtasks:
                            done_class = "done" if subtask.done else ""
                            checkbox = "☑" if subtask.done else "☐"
                            yield Static(f"  {checkbox} {subtask.title}", classes=f"big-task-subtask {done_class}")

        # Documents tabs
        yield Static("Documents", classes="detail-meta")
        with TabbedContent():
            for key in ProjectDetail.DOCUMENT_KEYS:
                doc = p.get_document(key)
                if doc and doc.exists:
                    tab_id = key
                    tab_label = doc.name
                    # Check if content exists
                    if doc.content.strip():
                        with TabPane(tab_label, id=tab_id):
                            panel = MarkdownDocumentPanel(tab_label, doc.content)
                            self._doc_panels[key] = panel
                            yield panel
                    else:
                        with TabPane(tab_label, id=tab_id):
                            panel = MarkdownDocumentPanel(tab_label, "(empty)")
                            self._doc_panels[key] = panel
                            yield panel

        # If no documents have content
        if not self._doc_panels:
            yield Static("No documents with content", classes="doc-empty")


class _ProjectsHeader(Static):
    """Header bar for Projects workspace."""
    DEFAULT_CSS = """
    _ProjectsHeader {
        border: round #a78bfa;
        background: $surface;
        padding: 0 2;
        margin: 0 0 1 0;
        height: auto;
        color: $text;
    }
    """

    def __init__(self, data: ProjectsData, **kwargs):
        project_count = len(data.projects)
        active_count = sum(1 for p in data.projects if p.status == "active")
        text = (
            f"[bold #a78bfa]Projects[/]  "
            f"[bold]{project_count}[/] projects  "
            f"[bold]{active_count}[/] active"
        )
        super().__init__(text, **kwargs)


class ProjectsView(WorkspaceView):
    """Projects workspace — project listing, selection, and detail view."""

    view_id = "projects"
    title = "Projects"

    def __init__(self, services, **kwargs):
        super().__init__(services, **kwargs)
        # Create the data provider
        self._provider = create_projects_provider(services)
        self._data: ProjectsData | None = None
        self._detail_panel: ProjectDetailPanel | None = None
        self._tree: ProjectTree | None = None

    def on_activate(self) -> None:
        """Load fresh data when view becomes active."""
        super().on_activate()
        self._load_data()

    def _load_data(self) -> None:
        """Load fresh data from provider."""
        self._data = self._provider.get_data(force_refresh=True)

    async def refresh_content(self) -> None:
        """Refresh all projects data and components."""
        self._load_data()
        await self._update_components()

    async def _update_components(self) -> None:
        """Update child components with fresh data."""
        if not self._data:
            return

        # Update project tree
        if self._tree:
            self._tree.update_data(self._data)

        # Update detail panel for selected project
        if self._detail_panel:
            selected = self._data.get_selected()
            if selected:
                self._detail_panel.update_project(selected)

    def _handle_project_select(self, project_name: str) -> None:
        """Handle project selection from tree."""
        if self._provider.select_project(project_name):
            # Preserve the selected project by not forcing a full rebuild
            # The provider's cached data already has the selection updated
            self._data = self._provider.get_data(force_refresh=False)
            self.call_later(self._update_components)

    def compose(self) -> ComposeResult:
        # Ensure we have initial data
        if self._data is None:
            self._load_data()

        # Header
        yield _ProjectsHeader(self._data or ProjectsData())

        # Main content area - two columns
        with Horizontal():
            # LEFT: Project Tree
            tree_ctx = self.context.child(
                breadcrumbs=["Projects", "Project List"],
                mode="explore",
            )
            self._tree = ProjectTree(
                context=tree_ctx,
                on_select=self._handle_project_select
            )
            if self._data:
                self._tree.update_data(self._data)
            yield self._tree

            # RIGHT: Project Detail
            detail_ctx = self.context.child(
                breadcrumbs=["Projects", "Project Detail"],
                mode="explore",
            )
            self._detail_panel = ProjectDetailPanel(context=detail_ctx)
            if self._data:
                selected = self._data.get_selected()
                if selected:
                    self._detail_panel.update_project(selected)
            yield self._detail_panel