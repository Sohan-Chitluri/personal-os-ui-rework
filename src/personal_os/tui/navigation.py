"""Centralized navigation state for the Personal OS workspace shell.

This is the single source of truth for "where am I" in the app. Widgets consume
this state; they do NOT own it. The state deliberately carries fields that are
not yet wired to UI (selected_project, focused_panel) so future milestones can
build on it without a refactor.

History infrastructure is present now (back/forward stacks) even though no
Back/Forward UI exists yet — the goal is to make that UI additive later.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# The canonical ordered set of workspaces. Order drives sidebar rendering.
NAV_ITEMS: List[str] = [
    "today",
    "projects",
    "tasks",
    "journal",
    "knowledge",
    "search",
    "reviews",
    "favorites",
    "settings",
]

DEFAULT_VIEW = "today"

# Human-facing labels (rendering concern lives with the data it labels).
NAV_LABELS = {
    "today": "Today",
    "projects": "Projects",
    "tasks": "Tasks",
    "journal": "Journal",
    "knowledge": "Knowledge",
    "search": "Search",
    "reviews": "Reviews",
    "favorites": "Favorites",
    "settings": "Settings",
}


@dataclass
class NavigationState:
    """Owns the app's navigation. One instance lives on the App.

    active_view      : id of the currently displayed workspace.
    history          : linear back-stack of visited view ids (most recent last,
                       excluding the active view).
    forward_stack    : views popped by going Back, available to Forward.
    selected_project : reserved for future project-scoped views.
    focused_panel    : reserved for future multi-panel focus tracking.
    """

    active_view: str = DEFAULT_VIEW
    history: List[str] = field(default_factory=list)
    forward_stack: List[str] = field(default_factory=list)
    selected_project: Optional[str] = None
    focused_panel: Optional[str] = None

    _max_history: int = 100

    def navigate(self, view_id: str) -> bool:
        """Switch to ``view_id``. Returns True if the active view changed.

        Pushes the previous view onto history and clears the forward stack
        (standard browser semantics). No-op (returns False) if already active.
        """
        if view_id == self.active_view:
            return False
        self.history.append(self.active_view)
        if len(self.history) > self._max_history:
            self.history.pop(0)
        self.forward_stack.clear()
        self.active_view = view_id
        return True

    @property
    def can_go_back(self) -> bool:
        return bool(self.history)

    @property
    def can_go_forward(self) -> bool:
        return bool(self.forward_stack)

    def back(self) -> Optional[str]:
        """Go to the previous view. Returns the new active view id, or None."""
        if not self.history:
            return None
        self.forward_stack.append(self.active_view)
        self.active_view = self.history.pop()
        return self.active_view

    def forward(self) -> Optional[str]:
        """Redo a Back. Returns the new active view id, or None."""
        if not self.forward_stack:
            return None
        self.history.append(self.active_view)
        self.active_view = self.forward_stack.pop()
        return self.active_view
