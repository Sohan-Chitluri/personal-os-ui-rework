"""BigTasksProvider — data layer for Big Tasks panels.

# B1: Vault Schema Specification (authoritative)

A Big Task is a multi-step, project-scoped deliverable with named subtasks.
It lives in the `## Big Tasks` section of a project's Tasks.md file.

## Vault format (per-project Tasks.md):

    ## Big Tasks

    ### <Big Task Title>
    <optional one-line description>
    - [ ] subtask one
    - [x] subtask two (completed)
    - [ ] subtask three

    ### <Another Big Task>
    - [ ] ...

Rules:
- The `## Big Tasks` section is optional. Projects without one have no big tasks.
- Each `### Title` line starts a new big task. Everything below until the next
  `###` or `##` belongs to that big task.
- Subtasks are standard checkbox items (`- [ ]` / `- [x]`).
- Non-checkbox lines between subtasks are treated as description/notes.
- No nesting — subtasks are flat (indented bullets are still flat logically).
- A big task with all subtasks checked is "complete"; with none checked is "blocked";
  otherwise it is "in-progress".

## Progress calculation:
    completed_subtasks / total_subtasks (as a float 0.0–1.0)
    If total_subtasks == 0, progress = 0.0 and status = "empty"

## Vault write format (used by KnowledgeApplier for B2):
    When creating a new Big Task, append to `## Big Tasks` section:

        ### {title}
        - [ ] {first_subtask}

    If `## Big Tasks` does not exist, create the section before appending.

# Implementation status: stub — see B2 for vault operations implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
import re

from personal_os.core.vault import VaultManager
from personal_os.core.config import get_config

_BIG_TASKS_SECTION_RE = re.compile(r'^##\s+Big Tasks\s*$', re.MULTILINE | re.IGNORECASE)
_SUBSECTION_RE = re.compile(r'^###\s+(.+)$')
_SECTION_RE = re.compile(r'^##\s+')
_CHECKBOX_RE = re.compile(r'^- \[([ xX])\]\s+(.+)$')


@dataclass
class BigTaskSubtask:
    done: bool
    title: str


@dataclass
class BigTask:
    title: str
    project: str
    subtasks: list[BigTaskSubtask] = field(default_factory=list)
    description: str = ""

    @property
    def total(self) -> int:
        return len(self.subtasks)

    @property
    def completed(self) -> int:
        return sum(1 for s in self.subtasks if s.done)

    @property
    def progress(self) -> float:
        return self.completed / self.total if self.total else 0.0

    @property
    def status(self) -> str:
        if self.total == 0:
            return "empty"
        if self.completed == self.total:
            return "complete"
        if self.completed == 0:
            return "blocked"
        return "in-progress"


@dataclass
class BigTasksData:
    big_tasks: list[BigTask] = field(default_factory=list)

    @property
    def total_count(self) -> int:
        return len(self.big_tasks)

    @property
    def in_progress_count(self) -> int:
        return sum(1 for t in self.big_tasks if t.status == "in-progress")


class BigTasksProvider:
    """Reads Big Tasks from per-project Tasks.md files per the B1 schema."""

    def __init__(self, vault: VaultManager, config: Any = None) -> None:
        self.vault = vault
        self.config = config or get_config()
        self._cached: Optional[BigTasksData] = None
        self._cache_valid = False

    def invalidate_cache(self) -> None:
        self._cache_valid = False

    def get_data(self, force_refresh: bool = False) -> BigTasksData:
        if not self._cache_valid or force_refresh or self._cached is None:
            self._cached = self._build_data()
            self._cache_valid = True
        return self._cached

    def _build_data(self) -> BigTasksData:
        all_tasks: list[BigTask] = []
        for project in self.vault.list_projects():
            tasks_file: Path = project["path"] / "Tasks.md"
            if not tasks_file.exists():
                continue
            try:
                content = self.vault.read_file(tasks_file)
            except Exception:
                continue
            all_tasks.extend(self._parse_big_tasks(content, project["name"]))

        # Sort: in-progress first, then blocked, then complete
        _order = {"in-progress": 0, "blocked": 1, "empty": 2, "complete": 3}
        all_tasks.sort(key=lambda t: (_order.get(t.status, 9), t.project.lower()))
        return BigTasksData(big_tasks=all_tasks)

    def _parse_big_tasks(self, content: str, project_name: str) -> list[BigTask]:
        # Find the ## Big Tasks section
        m = _BIG_TASKS_SECTION_RE.search(content)
        if not m:
            return []

        section_text = content[m.end():]
        # Trim at next ## section
        next_section = _SECTION_RE.search(section_text)
        if next_section:
            section_text = section_text[: next_section.start()]

        tasks: list[BigTask] = []
        current: Optional[BigTask] = None

        for line in section_text.splitlines():
            stripped = line.strip()
            sub_m = _SUBSECTION_RE.match(stripped)
            if sub_m:
                if current is not None:
                    tasks.append(current)
                current = BigTask(title=sub_m.group(1).strip(), project=project_name)
                continue

            if current is None:
                continue

            cb_m = _CHECKBOX_RE.match(stripped)
            if cb_m:
                done = cb_m.group(1).lower() == "x"
                current.subtasks.append(BigTaskSubtask(done=done, title=cb_m.group(2).strip()))
            elif stripped and not current.description:
                current.description = stripped

        if current is not None:
            tasks.append(current)

        return tasks


def create_big_tasks_provider(app_services: Any) -> BigTasksProvider:
    return BigTasksProvider(vault=app_services.vault, config=app_services.config)
