"""Centralized navigation state for the Personal OS workspace shell.

This is the single source of truth for "where am I" in the app. Widgets consume
this state; they do NOT own it.

NavigationState is responsible ONLY for shell-level navigation:
- active workspace (the center pane view)
- history (back stack)
- forward stack (forward navigation)

Workspace-local navigation (selected_project, selected_document, panel_focus,
breadcrumbs) is deliberately NOT here — it belongs in WorkspaceContext and
will be managed by workspace-local navigation in a future milestone. Keeping
NavigationState shell-only makes the shell/workspace boundary clean and the
future workspace-local navigation additive (no refactor needed).
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
    "projects": "Big Tasks",
    "tasks": "Tasks",
    "journal": "Journal",
    "knowledge": "Knowledge",
    "search": "Search",
    "reviews": "Reviews",
    "favorites": "Favorites",
    "settings": "Settings",
}

# Icon glyphs shown alongside each label in the sidebar.
NAV_ICONS = {
    "today": "\U0001F3E0",       # house
    "projects": "\U0001F4C1",    # folder
    "tasks": "\U0001F4CB",       # clipboard
    "journal": "\U0001F4D4",     # notebook
    "knowledge": "\U0001F9E0",   # brain
    "search": "\U0001F50D",      # magnifier
    "reviews": "\U0001F4CA",     # bar chart
    "favorites": "\u2B50",       # star
    "settings": "\u2699",        # gear
}


@dataclass
class NavigationState:
    """Owns the app's SHELL navigation. One instance lives on the App.

    active_view      : id of the currently displayed workspace.
    history          : linear back-stack of visited view ids (most recent last,
                       excluding the active view).
    forward_stack    : views popped by going Back, available to Forward.

    Workspace-local fields (selected_project, focused_panel, breadcrumbs,
    active_document) are intentionally NOT here — they belong in
    WorkspaceContext and will be managed by workspace-local navigation
    in a future milestone.
    """

    active_view: str = DEFAULT_VIEW
    history: List[str] = field(default_factory=list)
    forward_stack: List[str] = field(default_factory=list)

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