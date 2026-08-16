"""AppServices — aggregates shared application services for dependency injection.

This abstraction groups the shared services (vault, config, processor, context_factory)
to reduce constructor parameter growth and simplify future dependency injection.
It does NOT change behavior — it's a pure grouping convenience.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from personal_os.tui.context import ContextFactory
from personal_os.tui.navigation import NavigationState


@dataclass
class AppServices:
    """Container for shared application services.

    Having a single services object passed to constructors (instead of 4-5
    individual parameters) makes the codebase more maintainable as new
    services are added. It also provides a clean injection point for tests.
    """

    vault: Any
    config: Any
    processor: Any
    context_factory: ContextFactory
    nav: NavigationState

    @classmethod
    def from_app(cls, vault: Any, config: Any, processor: Any,
                 context_factory: ContextFactory, nav: NavigationState) -> "AppServices":
        return cls(
            vault=vault,
            config=config,
            processor=processor,
            context_factory=context_factory,
            nav=nav,
        )

    def build_context(self, nav_state: NavigationState, workspace_id: str) -> Any:
        """Build a fresh WorkspaceContext for the given workspace.

        This is the single authoritative path for context creation.
        The router calls this when switching views.
        """
        # Temporarily set the active view for context building
        original_view = nav_state.active_view
        nav_state.active_view = workspace_id
        try:
            return self.context_factory.build(nav_state)
        finally:
            nav_state.active_view = original_view