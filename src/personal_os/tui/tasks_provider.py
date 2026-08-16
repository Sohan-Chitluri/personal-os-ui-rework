"""AllTasksProvider — data layer for the Tasks workspace.

Reads every Tasks.md in the vault (all projects + Inbox), parses open and
completed checkbox items, and groups them by project for the TasksView.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from personal_os.core.vault import VaultManager
from personal_os.core.config import get_config

_CHECKBOX_RE = re.compile(r'^- \[([ xX])\]\s+(.+)$')


@dataclass
class TaskItem:
    done: bool
    title: str
    project: str


@dataclass
class ProjectTaskGroup:
    project: str
    pending: list[TaskItem] = field(default_factory=list)
    completed: list[TaskItem] = field(default_factory=list)

    @property
    def pending_count(self) -> int:
        return len(self.pending)

    @property
    def completed_count(self) -> int:
        return len(self.completed)


@dataclass
class AllTasksData:
    groups: list[ProjectTaskGroup] = field(default_factory=list)
    total_pending: int = 0
    total_completed: int = 0


class AllTasksProvider:
    """Reads and groups all vault tasks. Single source of truth for TasksView."""

    def __init__(self, vault: VaultManager, config: Any = None) -> None:
        self.vault = vault
        self.config = config or get_config()
        self._cached: Optional[AllTasksData] = None
        self._cache_valid = False

    def invalidate_cache(self) -> None:
        self._cache_valid = False

    def get_data(self, force_refresh: bool = False) -> AllTasksData:
        if not self._cache_valid or force_refresh or self._cached is None:
            self._cached = self._build_data()
            self._cache_valid = True
        return self._cached

    def _build_data(self) -> AllTasksData:
        groups: list[ProjectTaskGroup] = []

        # Project tasks
        for project in self.vault.list_projects():
            tasks_file: Path = project["path"] / "Tasks.md"
            if not tasks_file.exists():
                continue
            group = self._parse_tasks_file(tasks_file, project["name"])
            if group.pending or group.completed:
                groups.append(group)

        # Inbox tasks
        inbox_file = self.vault.config.inbox_dir / "Tasks.md"
        if inbox_file.exists():
            group = self._parse_tasks_file(inbox_file, "Inbox")
            if group.pending or group.completed:
                groups.append(group)

        # Sort: projects with most pending tasks first; Inbox always last
        def _sort_key(g: ProjectTaskGroup) -> tuple:
            return (g.project == "Inbox", -g.pending_count, g.project.lower())

        groups.sort(key=_sort_key)

        total_pending = sum(g.pending_count for g in groups)
        total_completed = sum(g.completed_count for g in groups)
        return AllTasksData(
            groups=groups,
            total_pending=total_pending,
            total_completed=total_completed,
        )

    def _parse_tasks_file(self, path: Path, project_name: str) -> ProjectTaskGroup:
        group = ProjectTaskGroup(project=project_name)
        try:
            content = self.vault.read_file(path)
        except Exception:
            return group

        for line in content.splitlines():
            m = _CHECKBOX_RE.match(line.strip())
            if not m:
                continue
            done = m.group(1).lower() == "x"
            title = m.group(2).strip()
            item = TaskItem(done=done, title=title, project=project_name)
            if done:
                group.completed.append(item)
            else:
                group.pending.append(item)

        return group


def create_tasks_provider(app_services: Any) -> AllTasksProvider:
    return AllTasksProvider(vault=app_services.vault, config=app_services.config)
