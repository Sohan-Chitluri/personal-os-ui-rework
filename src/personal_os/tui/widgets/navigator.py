"""NavigatorSidebar — persistent Yazi-inspired workspace navigator.

A single always-visible list of workspaces. Selection is SILENT: it posts a
NavigateRequested message and nothing else — no chat output, no AI processing.
The App consumes the message and drives NavigationState + ViewRouter.

Keyboard-first (Yazi feel): j/k and arrows move, Enter/l selects, number keys
jump. The sidebar consumes navigation state; it does not own it.
"""
from __future__ import annotations

from textual.binding import Binding
from textual.message import Message
from textual.widgets import ListView, ListItem, Label

from personal_os.tui.navigation import NAV_ITEMS, NAV_LABELS


class NavigateRequested(Message):
    """Emitted when the user selects a workspace in the navigator."""

    def __init__(self, view_id: str):
        super().__init__()
        self.view_id = view_id


class NavigatorSidebar(ListView):
    """Persistent left navigator listing all workspaces."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("l", "select_cursor", "Open", show=False),
        Binding("enter", "select_cursor", "Open", show=False),
    ]

    def __init__(self, **kwargs):
        items = [
            ListItem(Label(NAV_LABELS[v]), id=f"nav-{v}")
            for v in NAV_ITEMS
        ]
        super().__init__(*items, **kwargs)

    def highlight_view(self, view_id: str) -> None:
        """Move the cursor to reflect the active view (state-driven)."""
        try:
            idx = NAV_ITEMS.index(view_id)
        except ValueError:
            return
        if self.index != idx:
            self.index = idx

    def _view_id_from_item(self, item: ListItem):
        if item is not None and item.id and item.id.startswith("nav-"):
            return item.id[len("nav-"):]
        return None

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """User pressed Enter / l on an item — silent navigation."""
        view_id = self._view_id_from_item(event.item)
        if view_id:
            self.post_message(NavigateRequested(view_id))
