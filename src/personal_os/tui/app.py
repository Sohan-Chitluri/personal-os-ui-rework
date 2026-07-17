"""Main TUI application for Personal OS — v0.2 workspace shell.

Milestone 2 (architectural): the app is now a shell composed of
  - a persistent NavigatorSidebar (left)
  - a ViewRouter center pane hosting one WorkspaceView at a time
  - a persistent StatusBar (bottom)
Navigation is silent and driven by a single NavigationState. Conversation is no
longer the permanent center view. A minimal capture input is retained so
existing capture keeps working (persist-first via the M1 capture contract); the
full capture overlay arrives in a later milestone.

Deliberately NOT in this milestone: markdown rendering, dashboards, voice UI,
search implementation, split panes, themes, animations, conversation redesign.
"""
from __future__ import annotations

import asyncio
from typing import Dict, List

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Input
from textual.binding import Binding

from personal_os.core.config import get_config
from personal_os.core.vault import get_vault
from personal_os.core.processor import process_entry, BackgroundProcessor
from personal_os.core.capture import persist_capture

from personal_os.tui.navigation import (
    NavigationState, NAV_ITEMS, NAV_LABELS, DEFAULT_VIEW,
)
from personal_os.tui.router import ViewRouter
from personal_os.tui.widgets.navigator import NavigatorSidebar, NavigateRequested
from personal_os.tui.widgets.status_bar import StatusBar


class PersonalOSTUI(App):
    """Personal OS Terminal User Interface — workspace shell."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #body {
        height: 1fr;
        layout: horizontal;
    }

    #sidebar {
        width: 24;
        min-width: 18;
        max-width: 40;
        border-right: solid $primary;
        background: $surface;
    }

    #center {
        width: 1fr;
        layout: vertical;
    }

    #view-router {
        height: 1fr;
        padding: 1;
        overflow-y: auto;
    }

    .workspace-view {
        height: 1fr;
    }

    .workspace-placeholder {
        padding: 1;
        color: $text-muted;
    }

    #capture-input {
        height: 3;
        border: solid $primary;
        margin: 0 1 1 1;
    }

    #capture-input:focus {
        border: solid $accent;
    }

    #status-bar {
        height: 1;
        background: $boost;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=False),
        Binding("ctrl+b", "toggle_sidebar", "Toggle Sidebar"),
        Binding("tab", "focus_next_region", "Focus", show=False),
        Binding("ctrl+i", "focus_capture", "Capture", show=False),
        # Number keys jump to workspaces (Yazi-ish quick nav).
        *[
            Binding(str(i + 1), f"nav('{vid}')", NAV_LABELS[vid], show=False)
            for i, vid in enumerate(NAV_ITEMS)
        ],
    ]

    TITLE = "Personal OS"
    SUB_TITLE = "Workspace"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.config = get_config()
        self.vault = get_vault()
        self.processor = BackgroundProcessor(self.vault)
        self.nav = NavigationState(active_view=DEFAULT_VIEW)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield NavigatorSidebar(id="sidebar")
            with Vertical(id="center"):
                yield ViewRouter(vault=self.vault, config=self.config, id="view-router")
                yield Input(
                    placeholder="Capture… (Enter to save)",
                    id="capture-input",
                )
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        # Start background processor.
        asyncio.create_task(self.processor.start())
        # Show the default view and sync navigator + status bar.
        self._apply_active_view(initial=True)

    # ---------- Navigation (silent) ----------

    def _apply_active_view(self, initial: bool = False) -> None:
        """Reflect NavigationState into the router, sidebar, and status bar."""
        view_id = self.nav.active_view
        router = self.query_one("#view-router", ViewRouter)
        router.show(view_id)

        sidebar = self.query_one("#sidebar", NavigatorSidebar)
        sidebar.highlight_view(view_id)

        status = self.query_one("#status-bar", StatusBar)
        status.set_workspace(NAV_LABELS.get(view_id, view_id.title()))
        status.set_mode("Explore")

    def action_nav(self, view_id: str) -> None:
        """Programmatic navigation (number keys). Silent."""
        if self.nav.navigate(view_id):
            self._apply_active_view()

    def on_navigate_requested(self, event: NavigateRequested) -> None:
        """Sidebar selection — silent workspace swap, no chat, no processing."""
        if self.nav.navigate(event.view_id):
            self._apply_active_view()

    # ---------- Shell actions ----------

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar", NavigatorSidebar)
        sidebar.display = not sidebar.display

    def action_focus_capture(self) -> None:
        self.query_one("#capture-input", Input).focus()

    def action_focus_next_region(self) -> None:
        self.screen.focus_next()

    # ---------- Capture (persist-first, M1 contract) ----------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "capture-input":
            return
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""

        status = self.query_one("#status-bar", StatusBar)

        # STAGE 1: persist raw — guaranteed before processing.
        receipt = persist_capture(text, source="Text", vault=self.vault)
        if not receipt.persisted:
            self.notify(f"Capture NOT saved: {receipt.error}", severity="error")
            return
        self.notify("Captured", timeout=2)

        # STAGE 2: process (best-effort). Raw already safe on disk.
        status.set_processor("processing")
        try:
            result = await process_entry(text, "Text")
            n = len(result.changes_made)
            self.notify(
                f"Processed: {n} change(s)" if n else "Logged (no actionable items)",
                timeout=3,
            )
        except Exception as e:  # noqa: BLE001
            self.notify(f"Processing error (raw is safe): {e}", severity="warning")
        finally:
            status.set_processor("idle")

    def on_unmount(self) -> None:
        asyncio.create_task(self.processor.stop())


# ---------- Entry points ----------

def run_tui() -> None:
    PersonalOSTUI().run()


if __name__ == "__main__":
    run_tui()
