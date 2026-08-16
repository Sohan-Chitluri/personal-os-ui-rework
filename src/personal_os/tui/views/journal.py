"""JournalView — the Journal workspace.

Provides date navigation and chronological display of journal entries
with original capture text and collapsible semantic metadata.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.widgets import Static

from personal_os.tui.views.base import WorkspaceView
from personal_os.tui.components.journal_entry import JournalList, JournalDateNavigator
from personal_os.tui.providers.journal_provider import (
    JournalProvider,
    JournalData,
    create_journal_provider,
)


class JournalView(WorkspaceView):
    """Journal workspace — date navigation and entry display with semantic metadata."""

    view_id = "journal"
    title = "Journal"

    def __init__(self, services, **kwargs):
        super().__init__(services, **kwargs)
        self._provider = create_journal_provider(services)
        self._data: JournalData | None = None
        self._list: JournalList | None = None
        self._navigator: JournalDateNavigator | None = None

    def on_activate(self) -> None:
        """Load fresh data when view becomes active."""
        super().on_activate()
        self._load_data()

    def _load_data(self) -> None:
        """Load fresh data from provider."""
        self._data = self._provider.get_data(force_refresh=True)

    async def refresh_content(self) -> None:
        """Refresh all journal data and components."""
        self._load_data()
        await self._update_components()

    async def _update_components(self) -> None:
        """Update child components with fresh data."""
        if not self._data:
            return

        # Update navigator
        if self._navigator:
            self._navigator.update_data(self._data)

        # Update list
        if self._list:
            self._list.update_data(self._data)

    def _handle_date_select(self, date: datetime) -> None:
        """Handle date selection from navigator."""
        if self._provider.select_date(date):
            # Preserve the selected date by not forcing a full rebuild
            self._data = self._provider.get_data(force_refresh=False)
            self.call_later(self._update_components)

    def _handle_toggle_semantic(self, capture_id: str) -> None:
        """Handle semantic toggle from entry."""
        self._provider.toggle_semantic(capture_id)
        self.call_later(self._update_components)

    def _handle_reprocess(self, capture_id: str) -> None:
        """Handle re-process action - stub."""
        # TODO: Trigger re-processing of the capture
        self.notify(f"Re-process requested for {capture_id[:8]}")

    def _handle_link_project(self, capture_id: str) -> None:
        """Handle link to project action - stub."""
        # TODO: Open project picker and link capture
        self.notify(f"Link to project requested for {capture_id[:8]}")

    def _handle_convert_task(self, capture_id: str) -> None:
        """Handle convert to task action - stub."""
        # TODO: Convert capture to a task
        self.notify(f"Convert to task requested for {capture_id[:8]}")

    def compose(self) -> ComposeResult:
        # Ensure we have initial data
        if self._data is None:
            self._load_data()

        # LEFT: Date Navigator
        nav_ctx = self.context.child(
            breadcrumbs=["Journal", "Dates"],
            mode="explore",
        )
        self._navigator = JournalDateNavigator(
            context=nav_ctx,
            on_date_select=self._handle_date_select
        )
        if self._data:
            self._navigator.update_data(self._data)
        yield self._navigator

        # RIGHT: Entry List
        list_ctx = self.context.child(
            breadcrumbs=["Journal", "Entries"],
            mode="explore",
        )
        self._list = JournalList(
            context=list_ctx,
            on_toggle_semantic=self._handle_toggle_semantic,
            on_reprocess=self._handle_reprocess,
            on_link_project=self._handle_link_project,
            on_convert_task=self._handle_convert_task
        )
        if self._data:
            self._list.update_data(self._data)
        yield self._list