"""Main TUI application for Personal OS — v0.2 workspace shell.

Milestone 2 (architectural): the app is now a shell composed of
  - a persistent NavigatorSidebar (left)
  - a ViewRouter center pane hosting one WorkspaceView at a time
  - a persistent StatusBar (bottom)
Navigation is silent and driven by a single NavigationState. Conversation is no
longer the permanent center view. A minimal capture input is retained so
existing capture keeps working (persist-first via the M1 capture contract); the
full capture overlay arrives in a later milestone.

Milestone 3.5 (architectural consolidation):
  - Removed legacy widgets/panels/* hierarchy
  - Introduced ContextFactory as single authoritative WorkspaceContext creator
  - Components receive child() contexts, not shared root context
  - NavigationState is shell-only (active_view, history, forward_stack)
  - AppServices aggregates shared services for cleaner DI

Deliberately NOT in this milestone: markdown rendering, dashboards, voice UI,
search implementation, split panes, themes, animations, conversation redesign.
"""
from __future__ import annotations

import asyncio
from typing import Dict, List, Any, Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Input, Static
from textual.binding import Binding
from textual.theme import Theme

# Custom dark theme — deep navy background with cyan/purple/orange/green accents,
# matching the Personal OS visual identity (see design reference screenshot).
PERSONAL_OS_THEME = Theme(
    name="personal-os",
    primary="#7dd3fc",       # cyan — links, borders, primary headers
    secondary="#c4b5fd",     # soft purple
    accent="#a78bfa",        # purple accent — greeting banner, highlights
    warning="#fbbf24",       # amber — pending / upcoming
    error="#f87171",         # soft red — overdue / errors
    success="#4ade80",       # green — done / focus
    foreground="#e2e8f0",
    background="#0a0e17",
    surface="#0f1420",
    panel="#131a2a",
    dark=True,
    variables={
        "border": "#1e293b",
        "block-cursor-foreground": "#0a0e17",
        "footer-key-foreground": "#a78bfa",
    },
)

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
from personal_os.tui.context import ContextFactory
from personal_os.tui.services import AppServices


from textual.message import Message

class RefreshDashboard(Message):
    """Message to trigger dashboard refresh."""
    pass


class PersonalOSTUI(App):
    """Personal OS Terminal User Interface — workspace shell."""

    CSS = """
    Screen {
        layout: vertical;
        background: $background;
    }

    Header {
        background: $panel;
        color: $primary;
    }

    #body {
        height: 1fr;
        layout: horizontal;
    }

    #sidebar {
        width: 22;
        min-width: 16;
        max-width: 36;
        border-right: solid $border;
        background: $surface;
        padding: 1 0;
    }

    #sidebar > ListItem {
        padding: 0 1;
        color: $text-muted;
    }

    #sidebar > ListItem.-highlight {
        background: $panel;
        color: $accent;
        text-style: bold;
    }

    #center {
        width: 1fr;
        layout: vertical;
    }

    #view-router {
        height: 1fr;
        padding: 1;
        overflow-y: auto;
        background: $background;
    }

    /* Dashboard grid column ratios: primary (left) wider, secondary (right) narrower */
    Horizontal {
        layout: horizontal;
    }
    Horizontal > _Card:first-child {
        width: 2fr;
    }
    Horizontal > _Card:last-child {
        width: 1fr;
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
        border: round $primary;
        background: $panel;
        margin: 0 1 1 1;
    }

    #capture-input:focus {
        border: round $accent;
    }

    #status-bar {
        height: 1;
        background: $panel;
        padding: 0 1;
        border-top: solid $border;
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
        self.register_theme(PERSONAL_OS_THEME)
        self.theme = "personal-os"
        self.config = get_config()
        self.vault = get_vault()
        self.processor = BackgroundProcessor(self.vault, on_processed=self._on_background_processed)
        self.nav = NavigationState(active_view=DEFAULT_VIEW)

        # Single authoritative ContextFactory owned by the App.
        self.context_factory = ContextFactory(self.vault, self.config)

        # AppServices aggregates shared services for cleaner dependency injection.
        self.services = AppServices.from_app(
            vault=self.vault,
            config=self.config,
            processor=self.processor,
            context_factory=self.context_factory,
            nav=self.nav,
        )

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield NavigatorSidebar(id="sidebar")
            with Vertical(id="center"):
                # Pass services to router instead of individual vault/config
                yield ViewRouter(services=self.services, id="view-router")
                yield Input(
                    placeholder="Capture... (Enter to save, Ctrl+I to focus)",
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
        """Reflect NavigationState into the router, sidebar, and status bar.

        Also refreshes the WorkspaceContext via ContextFactory when the
        active workspace changes.
        """
        view_id = self.nav.active_view
        router = self.query_one("#view-router", ViewRouter)
        router.show(view_id)

        sidebar = self.query_one("#sidebar", NavigatorSidebar)
        sidebar.highlight_view(view_id)

        status = self.query_one("#status-bar", StatusBar)
        status.set_workspace(NAV_LABELS.get(view_id, view_id.title()))
        status.set_mode("Explore")

    def _on_background_processed(self, result, text=None, source=None) -> None:
        """Called when background processor finishes processing an entry."""
        # Invalidate cache and refresh dashboard if today view is active
        if hasattr(self.services, '_today_provider') and self.services._today_provider:
            self.services._today_provider.invalidate_cache()
        self.post_message(RefreshDashboard())

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
            result = await self.processor.process(text, "Text", capture_id=receipt.capture_id)
            n = len(result.changes_made)
            self.notify(
                f"Processed: {n} change(s)" if n else "Logged (no actionable items)",
                timeout=3,
            )
            # Refresh dashboard after processing
            self.post_message(RefreshDashboard())
        except Exception as e:  # noqa: BLE001
            self.notify(f"Processing error (raw is safe): {e}", severity="warning")
        finally:
            status.set_processor("idle")

    async def on_refresh_dashboard(self, event: RefreshDashboard) -> None:
        """Handle dashboard refresh request."""
        router = self.query_one("#view-router", ViewRouter)
        if router._active_id == "today":
            view = router._views.get("today")
            if view and hasattr(view, 'refresh_content'):
                await view.refresh_content()

    def on_unmount(self) -> None:
        asyncio.create_task(self.processor.stop())


# ---------- Entry points ----------

def run_tui() -> None:
    PersonalOSTUI().run()


if __name__ == "__main__":
    run_tui()