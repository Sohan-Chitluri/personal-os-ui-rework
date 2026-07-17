"""ViewRouter — hosts exactly one active WorkspaceView in the center pane.

The router knows only which view is active (by id), not any view's internals.
It lazily instantiates views from VIEW_REGISTRY, mounts the active one, and
drives the WorkspaceView lifecycle (on_activate / on_deactivate / refresh).

Router state is derived from NavigationState; the router does not own navigation.
"""
from __future__ import annotations

from typing import Dict, Optional

from textual.containers import Container

from personal_os.tui.views import VIEW_REGISTRY, WorkspaceView


class ViewRouter(Container):
    """Container that swaps the active workspace view."""

    def __init__(self, vault=None, config=None, **kwargs):
        super().__init__(**kwargs)
        self.vault = vault
        self.config = config
        self._views: Dict[str, WorkspaceView] = {}
        self._active_id: Optional[str] = None

    def _get_or_create(self, view_id: str) -> Optional[WorkspaceView]:
        if view_id not in VIEW_REGISTRY:
            return None
        if view_id not in self._views:
            cls = VIEW_REGISTRY[view_id]
            self._views[view_id] = cls(vault=self.vault, config=self.config)
        return self._views[view_id]

    @property
    def active_id(self) -> Optional[str]:
        return self._active_id

    def show(self, view_id: str) -> bool:
        """Make ``view_id`` the active view. Returns True on success.

        Mounts the view if needed, hides the previous, runs lifecycle hooks.
        Silent by design: no messages, no processing — pure view swap.
        """
        view = self._get_or_create(view_id)
        if view is None:
            return False

        if self._active_id == view_id:
            return True

        # Deactivate previous
        if self._active_id is not None:
            prev = self._views.get(self._active_id)
            if prev is not None:
                prev.on_deactivate()
                prev.display = False

        # Mount if first time shown
        if not view.is_mounted:
            self.mount(view)
        view.display = True
        view.on_activate()
        self._active_id = view_id
        return True

    async def refresh_active(self) -> None:
        if self._active_id is not None:
            view = self._views.get(self._active_id)
            if view is not None:
                await view.refresh_content()
