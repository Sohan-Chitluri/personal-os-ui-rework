"""SearchView — the Search workspace.

Provides live search across the vault with filters and grouped results.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from textual.app import ComposeResult

from personal_os.tui.views.base import WorkspaceView
from personal_os.tui.components.search_results import SearchResults
from personal_os.tui.providers.search_provider import (
    SearchResultsData,
    create_search_provider,
)


class SearchView(WorkspaceView):
    """Search workspace — live search across the vault."""

    view_id = "search"
    title = "Search"

    def __init__(self, services, **kwargs):
        super().__init__(services, **kwargs)
        self._provider = create_search_provider(services)
        self._data: SearchResultsData | None = None
        self._results: SearchResults | None = None
        self._search_task: Optional[asyncio.Task] = None
        self._debounce_delay = 0.15  # 150ms debounce
        self._last_query = ""

    def on_activate(self) -> None:
        """Load fresh data when view becomes active."""
        super().on_activate()
        if self._data is None:
            self._data = SearchResultsData()

    async def refresh_content(self) -> None:
        """Refresh search results."""
        if self._data and self._data.query:
            await self._perform_search(self._data.query)

    async def _perform_search(self, query: str) -> None:
        """Perform the search with debouncing."""
        if self._search_task:
            self._search_task.cancel()
        
        self._data = SearchResultsData(query=query, is_searching=True)
        await self._update_components()
        
        async def do_search():
            try:
                await asyncio.sleep(self._debounce_delay)
                self._data = await self._provider.search(query, limit=100)
                await self._update_components()
            except asyncio.CancelledError:
                pass
        
        self._search_task = asyncio.create_task(do_search())

    def _on_search_change(self, query: str) -> None:
        """Handle live search input change with debouncing."""
        # Only search if query changed
        if query == self._last_query:
            return
        self._last_query = query
        # Schedule the async search
        asyncio.create_task(self._perform_search(query))

    def _on_search_submit(self, query: str) -> None:
        """Handle search submit (Enter key)."""
        self._last_query = query
        asyncio.create_task(self._perform_search(query))

    async def _update_components(self) -> None:
        """Update child components with fresh data."""
        if self._results and self._data:
            self._results.update_data(self._data)

    def _handle_result_select(self, item) -> None:
        """Handle search result selection - open in appropriate context."""
        # Navigate to the appropriate view based on file type
        if self.app is None:
            return
            
        file_path = item.file
        
        # Journal files - navigate to Journal view
        if "Journal" in file_path:
            self.app.action_nav("journal")
            # TODO: Scroll to the specific date entry
            if self.app:
                self.app.notify(f"Opened journal: {file_path}")
            return
        
        # Project files - navigate to Projects view
        if "02-Projects" in file_path:
            self.app.action_nav("projects")
            # TODO: Select the specific project and file
            if self.app:
                self.app.notify(f"Opened project file: {file_path}")
            return
        
        # Inbox/Resources/Knowledge - navigate to Knowledge view
        if any(folder in file_path for folder in ["00-Inbox", "01-Academic", "03-Skills", "05-Resources", "06-Archive"]):
            self.app.action_nav("knowledge")
            if self.app:
                self.app.notify(f"Opened knowledge file: {file_path}")
            return
        
        # Default - just notify
        if self.app:
            self.app.notify(f"Selected: {item.file}:{item.line}")

    def compose(self) -> ComposeResult:
        if self._data is None:
            self._data = SearchResultsData()
        
        results_ctx = self.context.child(
            breadcrumbs=["Search", "Results"],
            mode="explore",
        )
        self._results = SearchResults(
            context=results_ctx,
            on_select=self._handle_result_select,
            on_search_change=self._on_search_change,
            on_search_submit=self._on_search_submit,
        )
        yield self._results