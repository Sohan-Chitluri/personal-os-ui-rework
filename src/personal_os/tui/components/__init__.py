"""Reusable UI components.

Components are the leaves of the Workspace -> Layout -> Panels -> Components
hierarchy. They are NOT workspaces and are never reachable from the navigator.
They are embeddable inside any workspace and depend only on a WorkspaceContext.

This milestone ships placeholder implementations that define the interfaces
future workspaces will use; real rendering/data arrives later.
"""
from personal_os.tui.components.base import Component
from personal_os.tui.components.markdown_renderer import MarkdownRenderer
from personal_os.tui.components.conversation_panel import ConversationPanel
from personal_os.tui.components.dashboard_panel import DashboardPanel

__all__ = [
    "Component",
    "MarkdownRenderer",
    "ConversationPanel",
    "DashboardPanel",
]
