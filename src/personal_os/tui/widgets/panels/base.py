"""Base sidebar panel class."""
from __future__ import annotations
from textual.widget import Widget
from textual.worker import Worker

from personal_os.core.vault import VaultManager


class SidebarPanel(Widget):
    """Base class for sidebar panels."""
    
    def __init__(self, title: str, panel_id: str, vault: VaultManager = None, **kwargs):
        super().__init__(**kwargs)
        self.panel_title = title
        self.panel_id = panel_id
        self.vault = vault
        self.can_focus = True
    
    async def refresh_content(self):
        """Refresh panel content - override in subclasses."""
        pass