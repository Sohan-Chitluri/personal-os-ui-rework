"""Workspace view contract.

Every workspace (Today, Projects, Tasks, ...) is a WorkspaceView. The
ViewRouter interacts with views ONLY through this contract — it never touches a
view's internals. New workspace types can be added without modifying the router.

A workspace composes reusable Components (Workspace -> Layout -> Panels ->
Components) and passes them a WorkspaceContext. The router still switches
WORKSPACES only; it knows nothing about layouts, panels, or components.

Lifecycle:
    __init__(vault, config)   construct (cheap; no I/O)
    on_activate()             called each time the view becomes active
    refresh_content()         (async) reload the view's content
    on_deactivate()           called when the view is replaced
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from personal_os.tui.context import WorkspaceContext


class WorkspaceView(Vertical):
    """Base class for all center-pane workspaces."""

    # Identity — overridden by subclasses.
    view_id: str = "base"
    title: str = "Workspace"

    DEFAULT_CLASSES = "workspace-view"

    def __init__(self, vault=None, config=None, **kwargs):
        kwargs.setdefault("classes", self.DEFAULT_CLASSES)
        super().__init__(**kwargs)
        self.vault = vault
        self.config = config
        self._activated = False
        self.context = self._build_context()

    def _build_context(self) -> WorkspaceContext:
        """Construct the WorkspaceContext this workspace hands to components."""
        return WorkspaceContext(
            workspace_id=self.view_id,
            mode="explore",
            vault=self.vault,
            config=self.config,
        )

    def compose(self) -> ComposeResult:
        yield Static(self._placeholder_text(), classes="workspace-placeholder")

    def _placeholder_text(self) -> str:
        return f"[ {self.title} ]\n\n(placeholder — implemented in a later milestone)"

    # ----- lifecycle (router-driven) -----

    def on_activate(self) -> None:
        """Called when this view becomes the active workspace."""
        self._activated = True

    def on_deactivate(self) -> None:
        """Called when this view stops being the active workspace."""
        self._activated = False

    async def refresh_content(self) -> None:
        """Reload content. Placeholder views have nothing to reload."""
        return None
