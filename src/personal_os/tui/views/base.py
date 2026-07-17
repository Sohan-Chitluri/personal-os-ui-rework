"""Workspace view contract.

Every workspace (Today, Projects, Tasks, ...) is a WorkspaceView. The
ViewRouter interacts with views ONLY through this contract — it never touches a
view's internals. This lets new workspace types be added without modifying the
router.

Lifecycle:
    __init__(vault, config)   construct (cheap; no I/O)
    on_activate()             called each time the view becomes active
    refresh()                 (async) reload the view's content
    on_deactivate()           called when the view is replaced

This milestone is architectural: concrete views are placeholders. Real content
(markdown, dashboards, search) arrives in later milestones by overriding these
hooks — no router changes needed.
"""
from __future__ import annotations

from typing import Optional

from textual.containers import Vertical
from textual.widgets import Static


class WorkspaceView(Vertical):
    """Base class for all center-pane workspaces.

    Subclasses set ``view_id`` and ``title`` and override the lifecycle hooks
    and/or compose(). The default implementation renders a simple placeholder so
    the framework is testable before real views exist.
    """

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

    def compose(self):
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
