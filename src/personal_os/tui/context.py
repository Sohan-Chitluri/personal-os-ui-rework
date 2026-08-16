"""WorkspaceContext — the information a workspace hands to its components.

Components depend on a WorkspaceContext, NOT on global application state. This
keeps components reusable and embeddable in any workspace, and makes the
Workspace -> Layout -> Panels -> Components hierarchy testable in isolation.

The ContextFactory is the single authoritative place where WorkspaceContext
is created. It owns the logic for building a context from NavigationState,
vault, and config. The App calls ContextFactory.build() when the active
workspace changes.

Child contexts (WorkspaceContext.child()) are the standard mechanism for
scoping a context to a component/sub-region. Components should NEVER share
the root WorkspaceContext instance directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional


@dataclass
class WorkspaceContext:
    """Per-workspace context consumed by components.

    active_project  : project the workspace is scoped to, if any.
    active_document : document/file the workspace is focused on, if any.
    breadcrumbs     : navigation trail for display (workspace-local).
    mode            : interaction mode ("explore", "capture", ...).
    vault / config  : shared services (read-only from a component's view).
    """

    workspace_id: str = ""
    active_project: Optional[str] = None
    active_document: Optional[Path] = None
    breadcrumbs: List[str] = field(default_factory=list)
    mode: str = "explore"
    vault: Any = None
    config: Any = None

    def child(self, **overrides) -> "WorkspaceContext":
        """Return a shallow copy with overrides — for scoping a sub-region.

        Components should NEVER share the root WorkspaceContext instance
        directly. Use child() to create a scoped context for each component.
        """
        data = {
            "workspace_id": self.workspace_id,
            "active_project": self.active_project,
            "active_document": self.active_document,
            "breadcrumbs": list(self.breadcrumbs),
            "mode": self.mode,
            "vault": self.vault,
            "config": self.config,
        }
        data.update(overrides)
        return WorkspaceContext(**data)


class ContextFactory:
    """Single authoritative factory for building WorkspaceContext instances.

    The App owns one ContextFactory. When NavigationState.active_view changes,
    the App calls ContextFactory.build() to produce a fresh context for the
    new active workspace. This ensures:
    - One consistent creation path.
    - Context is refreshed whenever the active workspace changes.
    - Future workspace-local navigation (breadcrumbs, active_project,
      active_document) can be added to NavigationState without changing
      the context creation logic.
    """

    def __init__(self, vault: Any, config: Any):
        self.vault = vault
        self.config = config

    def build(self, nav_state) -> WorkspaceContext:
        """Build a fresh WorkspaceContext for the currently active workspace.

        Args:
            nav_state: NavigationState instance (contains active_view and
                       future workspace-local fields like selected_project).

        Returns:
            A new WorkspaceContext instance bound to nav_state.active_view.
        """
        return WorkspaceContext(
            workspace_id=nav_state.active_view,
            mode="explore",
            vault=self.vault,
            config=self.config,
            # Workspace-local navigation fields (reserved for future milestones):
            # active_project=nav_state.selected_project,
            # active_document=nav_state.selected_document,
            # breadcrumbs=nav_state.breadcrumbs,
        )


__all__ = ["WorkspaceContext", "ContextFactory"]