"""TodayView — the first composed workspace.

Demonstrates the composition hierarchy:

    Workspace (TodayView)
      -> Layout   (vertical regions)
        -> Panels (labelled regions)
          -> Components (reusable: DashboardPanel, MarkdownRenderer, ...)

The router still only switches workspaces; all composition happens here, inside
the workspace, using reusable components bound to this workspace's
WorkspaceContext. Placeholder regions only — no real data (per milestone scope).
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from personal_os.tui.views.base import WorkspaceView
from personal_os.tui.components import DashboardPanel, ConversationPanel


class _Region(Vertical):
    """A labelled layout region that hosts one or more components."""

    def __init__(self, label: str, *children, **kwargs):
        kwargs.setdefault("classes", "today-region")
        super().__init__(**kwargs)
        self._label = label
        self._children_widgets = children

    def compose(self):
        yield Static(self._label, classes="region-title")
        for child in self._children_widgets:
            yield child


class TodayView(WorkspaceView):
    """Composed home workspace."""

    view_id = "today"
    title = "Today"

    def compose(self) -> ComposeResult:
        ctx = self.context
        # Dashboard region — hosts the reusable DashboardPanel component.
        yield _Region("Dashboard", DashboardPanel(context=ctx))
        # Recent captures region — placeholder region (real data later).
        yield _Region(
            "Recent Captures",
            Static("(no data yet — placeholder)", classes="component-placeholder"),
        )
        # Pending processing region — placeholder.
        yield _Region(
            "Pending Processing",
            Static("(no data yet — placeholder)", classes="component-placeholder"),
        )
        # Current project region — will host a MarkdownRenderer/Conversation later;
        # embeds the ConversationPanel component to prove conversation is embeddable
        # (never a standalone workspace).
        yield _Region("Current Project", ConversationPanel(context=ctx))
