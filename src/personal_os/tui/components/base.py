"""Component base class.

A Component is a reusable, embeddable UI leaf. It depends only on a
WorkspaceContext (never on global app state) so it can be dropped into any
workspace. Components are NOT workspaces and are never registered with the
navigator or router.
"""
from __future__ import annotations

from typing import Optional

from textual.containers import Vertical
from textual.widgets import Static

from personal_os.tui.context import WorkspaceContext


class Component(Vertical):
    """Base class for embeddable workspace components."""

    # Overridden by subclasses for identity/labelling.
    component_id: str = "component"
    title: str = "Component"

    def __init__(self, context: Optional[WorkspaceContext] = None, **kwargs):
        kwargs.setdefault("classes", "component")
        super().__init__(**kwargs)
        self.context = context or WorkspaceContext()

    def compose(self):
        yield Static(self._placeholder_text(), classes="component-placeholder")

    def _placeholder_text(self) -> str:
        return f"[{self.title}] (placeholder)"

    def set_context(self, context: WorkspaceContext) -> None:
        """Rebind this component to a new context."""
        self.context = context

    async def refresh_content(self) -> None:
        """Reload component content. Placeholders have nothing to reload."""
        return None
