"""Settings panel - configuration editor."""
from __future__ import annotations
from textual.widgets import ListView, ListItem, Label, Static, Input, Button, Switch
from textual.binding import Binding
from textual.message import Message

from personal_os.tui.widgets.panels.base import SidebarPanel
from personal_os.core.config import get_config


class SettingChanged(Message):
    """Posted when a setting is changed."""
    def __init__(self, section: str, key: str, value):
        super().__init__()
        self.section = section
        self.key = key
        self.value = value


class SettingsPanel(SidebarPanel):
    """Settings editor panel."""
    
    BINDINGS = [
        Binding("enter", "edit", "Edit"),
        Binding("s", "save", "Save"),
        Binding("r", "reset", "Reset"),
    ]
    
    def __init__(self, config, **kwargs):
        super().__init__("Settings", "settings", **kwargs)
        self.config = config
        self.editing_item = None
    
    def compose(self):
        yield Static("Settings", classes="panel-title")
        yield ListView(id="settings-list")
    
    async def refresh_content(self):
        """Refresh settings list."""
        list_view = self.query_one("#settings-list", ListView)
        list_view.clear()
        
        settings = [
            ("Vault", "path", str(self.config.vault.root)),
            ("Theme", "theme", self.config.ui.theme),
            ("Sidebar Width", "sidebar_width", f"{self.config.ui.sidebar_width}%"),
            ("Model Provider", "provider", self.config.model.provider),
            ("Model Name", "model_name", self.config.model.model_name),
            ("Voice Engine", "voice_engine", self.config.voice.engine),
            ("Voice Model", "voice_model", self.config.voice.model),
            ("Auto Process", "auto_process", str(self.config.processing.auto_process)),
        ]
        
        for section, key, value in settings:
            item = ListItem(
                Label(f"{section}: [cyan]{value}[/cyan]", classes="setting-item"),
                classes="setting-row"
            )
            item.data = {"section": section, "key": key, "value": value}
            await list_view.append(item)
    
    def on_list_view_selected(self, event: ListView.Selected):
        """Handle setting selection."""
        if event.item.data:
            self.editing_item = event.item.data
            self._edit_setting(event.item.data)
    
    def _edit_setting(self, data: dict):
        """Edit a setting value."""
        self.app.post_message(self.app.EditSettingRequested(
            data["section"], data["key"], data["value"]
        ))
    
    def action_edit(self):
        """Edit selected setting."""
        list_view = self.query_one("#settings-list", ListView)
        if list_view.highlighted_child and list_view.highlighted_child.data:
            self._edit_setting(list_view.highlighted_child.data)
    
    def action_save(self):
        """Save configuration."""
        self.config.save()
        self.app.notify("Settings saved")
    
    def action_reset(self):
        """Reset to defaults."""
        self.app.post_message(self.app.ResetSettingsRequested())