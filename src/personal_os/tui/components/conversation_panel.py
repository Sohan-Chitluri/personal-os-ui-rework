"""ConversationPanel — reusable, embeddable conversation component.

IMPORTANT (design direction): Personal OS is context-first, not chat-first.
Conversation is NOT a workspace and is NOT reachable from the navigator. This
component exists purely to be embedded inside other workspaces later (e.g. a
project workspace, or the Today workspace). Placeholder implementation only —
no AI conversation logic this milestone.
"""
from __future__ import annotations

from textual.widgets import Static

from personal_os.tui.components.base import Component


class ConversationPanel(Component):
    """An embeddable conversation surface (placeholder)."""

    component_id = "conversation-panel"
    title = "Conversation"

    def compose(self):
        yield Static(
            "[Conversation] embeddable component (placeholder)\n"
            "Not a workspace; not reachable from the navigator.",
            classes="component-placeholder",
        )
