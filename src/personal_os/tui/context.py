"""WorkspaceContext — the information a workspace hands to its components.

Components depend on a WorkspaceContext, NOT on global application state. This
keeps components reusable and embeddable in any workspace, and makes the
Workspace → Layout → Panels → Components hierarchy testable in isolation.

The App builds a WorkspaceContext (from NavigationState + vault/config) and
passes it down when composing a workspace. Fields are intentionally broad and
some are reserved for later milestones (active_document, breadcrumbs).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


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
    vault: object = None
    config: object = None

    def child(self, **overrides) -> "WorkspaceContext":
        """Return a shallow copy with overrides — for scoping a sub-region."""
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
