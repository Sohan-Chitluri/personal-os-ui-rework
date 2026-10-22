"""Typed application tools for Personal OS AI Assistant.

Enforces:
- Strict authenticated owner scoping on every query and mutation.
- Server-side argument validation via Pydantic schemas.
- Optimistic locking revision checks for mutations.
- Safe read-only operations for task/project exploration and date resolution.
- Structured action proposals with explicit confirmation gates for destructive
  (e.g., delete) or ambiguous multi-record operations.
- Application-provided reference time for relative date resolution.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from personal_os.core.llm import ToolDefinition
from personal_os.services.interfaces import (
    ProjectService,
    ServiceConflictError,
    ServiceNotFound,
    TaskService,
)

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Tool Argument Schemas (Server-side validation)
# --------------------------------------------------------------------------


class SearchTasksInput(BaseModel):
    query: str | None = Field(default=None, description="Keywords to match in title/description")
    project_id: str | None = Field(default=None, description="Optional project ID to filter by")
    status: str | None = Field(default=None, description="Status filter: todo, in_progress, done")
    limit: int = Field(default=20, ge=1, le=100, description="Maximum number of tasks to return")


class GetTaskInput(BaseModel):
    task_id: str = Field(..., min_length=1, description="Unique task ID")


class ListProjectsInput(BaseModel):
    status: str | None = Field(default=None, description="Status filter: active, archived")
    limit: int = Field(default=50, ge=1, le=100, description="Max projects to return")


class GetProjectInput(BaseModel):
    project_id: str | None = Field(default=None, description="Project unique ID")
    name: str | None = Field(default=None, description="Project name")

    @field_validator("name", mode="after")
    @classmethod
    def _validate_either(cls, v: str | None, info: Any) -> str | None:
        return v


class ResolveRelativeDateInput(BaseModel):
    expression: str = Field(..., min_length=1, description="Relative date expression")


class ProposeTaskActionInput(BaseModel):
    action_type: Literal[
        "create_task", "update_task", "reschedule_task", "complete_task", "delete_task"
    ]
    task_id: str | None = Field(default=None, description="Task ID (required for mutations)")
    expected_revision: int | None = Field(
        default=None, description="Optimistic concurrency revision"
    )
    title: str | None = Field(default=None, description="Task title")
    description: str | None = Field(default=None, description="Task description")
    project_id: str | None = Field(default=None, description="Project ID or project name")
    due_date: str | None = Field(default=None, description="ISO datetime or date")
    priority: int | None = Field(default=None, ge=1, le=5, description="Priority 1-5")
    status: str | None = Field(default=None, description="Task status")
    requires_confirmation: bool = Field(
        default=False, description="Whether explicit user confirmation is required"
    )
    rationale: str = Field(default="", description="Reasoning for this action")


@dataclass
class ActionProposal:
    action_type: str
    parameters: dict[str, Any]
    requires_confirmation: bool
    rationale: str
    diff_summary: str
    validated: bool = True
    validation_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# Tool Definitions for Bedrock Converse
# --------------------------------------------------------------------------


def get_personal_os_tools() -> list[ToolDefinition]:
    """Return tool definitions exposed to the AI model."""
    return [
        ToolDefinition(
            name="search_tasks",
            description=(
                "Search and list existing tasks for the current user. "
                "Returns task ID, title, project, due date, status, and revision."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keyword search across task titles and descriptions",
                    },
                    "project_id": {"type": "string", "description": "Filter tasks by project ID"},
                    "status": {
                        "type": "string",
                        "enum": ["todo", "in_progress", "done"],
                        "description": "Filter by task status",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max tasks to return (1-100, default 20)",
                    },
                },
                "required": [],
            },
        ),
        ToolDefinition(
            name="get_task",
            description="Retrieve complete details for a specific task by its ID.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The unique task ID"},
                },
                "required": ["task_id"],
            },
        ),
        ToolDefinition(
            name="list_projects",
            description=(
                "List all projects belonging to the current user. "
                "Returns project ID, name, description, status, and revision."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["active", "archived"],
                        "description": "Filter by project status",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max projects to return (default 50)",
                    },
                },
                "required": [],
            },
        ),
        ToolDefinition(
            name="get_project",
            description="Retrieve details for a specific project by ID or exact name.",
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "The unique project ID"},
                    "name": {"type": "string", "description": "The project name"},
                },
                "required": [],
            },
        ),
        ToolDefinition(
            name="resolve_relative_date",
            description=(
                "Resolve relative date expressions (e.g. 'tomorrow', 'next Monday', "
                "'in 3 days', 'end of week') to an exact UTC ISO-8601 timestamp using the "
                "application reference time. Never invent or guess dates."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "The relative date string (e.g. 'tomorrow', 'in 2 days')",
                    },
                },
                "required": ["expression"],
            },
        ),
        ToolDefinition(
            name="propose_task_action",
            description=(
                "Generate a structured action proposal for creating, editing, rescheduling, "
                "or completing/deleting a task. For mutations, expected_revision is required. "
                "Destructive actions (like delete) or ambiguous multi-record operations are "
                "flagged for explicit user confirmation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action_type": {
                        "type": "string",
                        "enum": [
                            "create_task",
                            "update_task",
                            "reschedule_task",
                            "complete_task",
                            "delete_task",
                        ],
                        "description": "The action to perform",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Task ID (required for update/delete)",
                    },
                    "expected_revision": {
                        "type": "integer",
                        "description": "Current task revision for optimistic locking",
                    },
                    "title": {"type": "string", "description": "Task title"},
                    "description": {"type": "string", "description": "Task description"},
                    "project_id": {
                        "type": "string",
                        "description": "Associated project ID or exact project name",
                    },
                    "due_date": {
                        "type": "string",
                        "description": "Due date in ISO-8601 format (YYYY-MM-DD)",
                    },
                    "priority": {
                        "type": "integer",
                        "description": "Priority from 1 (highest) to 5 (lowest)",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["todo", "in_progress", "done"],
                        "description": "Status to set",
                    },
                    "requires_confirmation": {
                        "type": "boolean",
                        "description": "True for deletions or multi-record operations",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Clear rationale explaining why this action is proposed",
                    },
                },
                "required": ["action_type"],
            },
        ),
    ]


# --------------------------------------------------------------------------
# Date Resolution Helper
# --------------------------------------------------------------------------


def resolve_date_expression(expr: str, ref_time: datetime) -> tuple[datetime | None, str | None]:
    """Parse relative or absolute date expression relative to ref_time in UTC."""
    if not expr or not expr.strip():
        return None, "Empty expression"

    val = expr.strip()
    low = val.lower()

    if low in ("none", "null", "unset", "clear"):
        return None, None

    if ref_time.tzinfo is None:
        ref_time = ref_time.replace(tzinfo=UTC)

    # 1. Simple keywords
    if low in ("today", "tonight"):
        return ref_time.replace(hour=23, minute=59, second=59, microsecond=0), None
    if low == "tomorrow":
        next_day = ref_time + timedelta(days=1)
        return next_day.replace(hour=23, minute=59, second=59, microsecond=0), None
    if low == "yesterday":
        prev_day = ref_time - timedelta(days=1)
        return prev_day.replace(hour=23, minute=59, second=59, microsecond=0), None

    # 2. "in X days" / "in X weeks"
    in_days_match = re.match(r"^in\s+(\d+)\s+days?$", low)
    if in_days_match:
        days = int(in_days_match.group(1))
        target = ref_time + timedelta(days=days)
        return target.replace(hour=23, minute=59, second=59, microsecond=0), None

    in_weeks_match = re.match(r"^in\s+(\d+)\s+weeks?$", low)
    if in_weeks_match:
        weeks = int(in_weeks_match.group(1))
        target = ref_time + timedelta(weeks=weeks)
        return target.replace(hour=23, minute=59, second=59, microsecond=0), None

    # 3. "end of week" / "this weekend"
    if low in ("end of week", "this weekend", "weekend"):
        # Sunday of current week (assuming Monday is 0)
        days_ahead = 6 - ref_time.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        target = ref_time + timedelta(days=days_ahead)
        return target.replace(hour=23, minute=59, second=59, microsecond=0), None

    # 4. Weekday mentions: "next monday", "this friday", etc.
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    weekday_re = r"\b(this|next)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    weekday_match = re.search(weekday_re, low)
    if weekday_match:
        qualifier = weekday_match.group(1)
        target_wd = weekdays[weekday_match.group(2)]
        current_wd = ref_time.weekday()
        days_ahead = (target_wd - current_wd) % 7
        if days_ahead == 0:
            days_ahead = 7
        elif qualifier == "next" and target_wd > current_wd:
            days_ahead += 7
        target = ref_time + timedelta(days=days_ahead)
        return target.replace(hour=23, minute=59, second=59, microsecond=0), None

    # 5. ISO format parsing
    iso_clean = val.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt, None
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(val, fmt).replace(
                hour=23, minute=59, second=59, tzinfo=UTC
            ), None
        except ValueError:
            pass

    return None, f"Unrecognized date format: '{expr}'"


# --------------------------------------------------------------------------
# Safe Server-Side Tool Execution Engine
# --------------------------------------------------------------------------


class PersonalOSToolExecutor:
    """Executes validated application tools strictly scoped to the authenticated owner."""

    def __init__(
        self,
        owner_id: str,
        task_service: TaskService,
        project_service: ProjectService,
        ref_time: datetime | None = None,
    ) -> None:
        self.owner_id = owner_id
        self.task_service = task_service
        self.project_service = project_service
        self.ref_time = ref_time or datetime.now(UTC)
        if self.ref_time.tzinfo is None:
            self.ref_time = self.ref_time.replace(tzinfo=UTC)

    async def execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch and execute tool with server-side validation and owner scoping."""
        try:
            if name == "search_tasks":
                return await self._search_tasks(SearchTasksInput.model_validate(arguments))
            elif name == "get_task":
                return await self._get_task(GetTaskInput.model_validate(arguments))
            elif name == "list_projects":
                return await self._list_projects(ListProjectsInput.model_validate(arguments))
            elif name == "get_project":
                return await self._get_project(GetProjectInput.model_validate(arguments))
            elif name == "resolve_relative_date":
                return self._resolve_relative_date(
                    ResolveRelativeDateInput.model_validate(arguments)
                )
            elif name == "propose_task_action":
                return await self._propose_task_action(
                    ProposeTaskActionInput.model_validate(arguments)
                )
            else:
                return {"error": f"Unknown tool: '{name}'"}
        except Exception as exc:
            log.warning("Tool execution error in '%s': %s", name, exc, exc_info=True)
            return {"error": f"Tool execution failed: {exc}"}

    async def _search_tasks(self, params: SearchTasksInput) -> dict[str, Any]:
        filters: dict[str, Any] = {"limit": params.limit}
        if params.project_id:
            filters["project_id"] = params.project_id
        if params.status:
            filters["status"] = params.status

        tasks = await self.task_service.list_tasks(self.owner_id, **filters)

        matched = []
        for t in tasks:
            if params.query:
                q_low = params.query.lower()
                title_match = q_low in t.title.lower()
                desc_match = bool(t.description and q_low in t.description.lower())
                if not (title_match or desc_match):
                    continue

            matched.append(
                {
                    "id": t.id,
                    "title": t.title,
                    "project_id": t.project_id,
                    "status": t.status,
                    "priority": t.priority,
                    "due_date": t.due_date.isoformat() if t.due_date else None,
                    "revision": t.revision,
                }
            )

        return {"tasks": matched, "count": len(matched)}

    async def _get_task(self, params: GetTaskInput) -> dict[str, Any]:
        try:
            task = await self.task_service.get_task(self.owner_id, params.task_id)
            return {
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "project_id": task.project_id,
                "status": task.status,
                "priority": task.priority,
                "due_date": task.due_date.isoformat() if task.due_date else None,
                "revision": task.revision,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            }
        except ServiceNotFound:
            return {"error": f"Task '{params.task_id}' not found"}

    async def _list_projects(self, params: ListProjectsInput) -> dict[str, Any]:
        filters: dict[str, Any] = {"limit": params.limit}
        if params.status:
            filters["status"] = params.status

        projects = await self.project_service.list_projects(self.owner_id, **filters)
        return {
            "projects": [
                {
                    "id": p.id,
                    "name": p.name,
                    "description": p.description,
                    "status": p.status,
                    "revision": p.revision,
                }
                for p in projects
            ],
            "count": len(projects),
        }

    async def _get_project(self, params: GetProjectInput) -> dict[str, Any]:
        if params.project_id:
            try:
                p = await self.project_service.get_project(self.owner_id, params.project_id)
                return {
                    "id": p.id,
                    "name": p.name,
                    "description": p.description,
                    "status": p.status,
                    "revision": p.revision,
                }
            except ServiceNotFound:
                if not params.name:
                    params.name = params.project_id

        if params.name:
            all_projs = await self.project_service.list_projects(self.owner_id)
            exact = [p for p in all_projs if p.name.lower() == params.name.lower()]
            if len(exact) == 1:
                p = exact[0]
                return {
                    "id": p.id,
                    "name": p.name,
                    "description": p.description,
                    "status": p.status,
                    "revision": p.revision,
                }
            elif len(exact) > 1:
                return {
                    "error": f"Ambiguous project name '{params.name}' matches multiple projects",
                    "candidates": [p.name for p in exact],
                }

            # Partial match
            partial = [
                p
                for p in all_projs
                if params.name.lower() in p.name.lower() or p.name.lower() in params.name.lower()
            ]
            if len(partial) == 1:
                p = partial[0]
                return {
                    "id": p.id,
                    "name": p.name,
                    "description": p.description,
                    "status": p.status,
                    "revision": p.revision,
                }
            elif len(partial) > 1:
                return {
                    "error": f"Ambiguous project name '{params.name}' matches multiple projects",
                    "candidates": [p.name for p in partial],
                }
            return {"error": f"Project '{params.name}' not found"}

        return {"error": "Must specify either project_id or name"}

    def _resolve_relative_date(self, params: ResolveRelativeDateInput) -> dict[str, Any]:
        dt, err = resolve_date_expression(params.expression, self.ref_time)
        if err:
            return {
                "expression": params.expression,
                "reference_time": self.ref_time.isoformat(),
                "resolved": False,
                "error": err,
            }
        return {
            "expression": params.expression,
            "reference_time": self.ref_time.isoformat(),
            "resolved": True,
            "resolved_datetime": dt.isoformat() if dt else None,
            "resolved_date": dt.strftime("%Y-%m-%d") if dt else None,
        }

    async def _propose_task_action(self, params: ProposeTaskActionInput) -> dict[str, Any]:
        """Validate proposed action and construct verified ActionProposal."""
        action_type = params.action_type
        payload: dict[str, Any] = {"action_type": action_type}
        requires_confirmation = params.requires_confirmation

        # Parse and validate due_date if provided
        resolved_due_dt: datetime | None = None
        if params.due_date:
            dt, err = resolve_date_expression(params.due_date, self.ref_time)
            if err:
                return {
                    "status": "invalid_proposal",
                    "error": f"Invalid due date '{params.due_date}': {err}",
                }
            resolved_due_dt = dt
            payload["due_date"] = dt.isoformat() if dt else None

        # Resolve project_id if a name was provided instead of an ID
        resolved_project_id: str | None = None
        project_name_label: str | None = None
        if params.project_id:
            proj_res = await self._get_project(
                GetProjectInput(project_id=params.project_id, name=params.project_id)
            )
            if "id" in proj_res:
                resolved_project_id = proj_res["id"]
                project_name_label = proj_res["name"]
                payload["project_id"] = resolved_project_id
            else:
                err_msg = proj_res.get("error", "unknown error")
                return {
                    "status": "invalid_proposal",
                    "error": f"Project '{params.project_id}' could not be resolved: {err_msg}",
                }

        diff_summary = ""

        if action_type == "create_task":
            title = (params.title or "").strip()
            if not title:
                return {
                    "status": "invalid_proposal",
                    "error": "Task title is required for create_task",
                }
            payload["title"] = title
            payload["description"] = params.description
            payload["priority"] = params.priority or 3
            diff_summary = f"Create task: '{title}'"
            if project_name_label:
                diff_summary += f" in project '{project_name_label}'"
            if resolved_due_dt:
                diff_summary += f", due {resolved_due_dt.strftime('%Y-%m-%d %H:%M UTC')}"

        elif action_type in ("update_task", "reschedule_task", "complete_task", "delete_task"):
            if not params.task_id:
                return {
                    "status": "invalid_proposal",
                    "error": f"task_id is required for {action_type}",
                }
            payload["task_id"] = params.task_id

            if params.expected_revision is None:
                return {
                    "status": "invalid_proposal",
                    "error": (
                        f"expected_revision is required for {action_type} "
                        "to prevent concurrent conflicts"
                    ),
                }
            payload["expected_revision"] = params.expected_revision

            # Verify task exists and check revision
            try:
                existing_task = await self.task_service.get_task(self.owner_id, params.task_id)
            except ServiceNotFound:
                return {
                    "status": "invalid_proposal",
                    "error": f"Task '{params.task_id}' not found",
                }

            if existing_task.revision != params.expected_revision:
                return {
                    "status": "conflict",
                    "error": (
                        f"Task '{params.task_id}' has been modified concurrently. "
                        f"Expected revision {params.expected_revision}, "
                        f"but current is {existing_task.revision}."
                    ),
                    "current_revision": existing_task.revision,
                }

            if action_type == "delete_task":
                # Deletions are destructive and MUST require confirmation
                requires_confirmation = True
                diff_summary = f"DELETE task '{existing_task.title}' (ID: {existing_task.id})"

            elif action_type == "complete_task":
                diff_summary = f"Mark task '{existing_task.title}' as COMPLETED"

            elif action_type == "reschedule_task":
                if not resolved_due_dt and not params.due_date:
                    return {
                        "status": "invalid_proposal",
                        "error": "due_date is required for reschedule_task",
                    }
                old_due = existing_task.due_date.isoformat() if existing_task.due_date else "None"
                new_due = resolved_due_dt.isoformat() if resolved_due_dt else "None"
                diff_summary = (
                    f"Reschedule task '{existing_task.title}' from {old_due} to {new_due}"
                )

            elif action_type == "update_task":
                changes = []
                if params.title and params.title != existing_task.title:
                    changes.append(f"title: '{existing_task.title}' -> '{params.title}'")
                    payload["title"] = params.title
                if resolved_due_dt and resolved_due_dt != existing_task.due_date:
                    changes.append(f"due_date: -> {resolved_due_dt.isoformat()}")
                if resolved_project_id and resolved_project_id != existing_task.project_id:
                    changes.append(f"project_id: -> {resolved_project_id}")
                if params.status and params.status != existing_task.status:
                    changes.append(f"status: '{existing_task.status}' -> '{params.status}'")
                    payload["status"] = params.status
                if params.priority is not None and params.priority != existing_task.priority:
                    changes.append(f"priority: {existing_task.priority} -> {params.priority}")
                    payload["priority"] = params.priority
                diff_summary = f"Update task '{existing_task.title}': " + (
                    ", ".join(changes) if changes else "no changes"
                )

        proposal = ActionProposal(
            action_type=action_type,
            parameters=payload,
            requires_confirmation=requires_confirmation,
            rationale=params.rationale or f"Action {action_type} requested by assistant turn",
            diff_summary=diff_summary,
            validated=True,
        )

        return {
            "status": "proposal_generated",
            "proposal": proposal.to_dict(),
            "requires_confirmation": requires_confirmation,
            "diff_summary": diff_summary,
        }

    async def apply_proposal(
        self, proposal_dict: dict[str, Any], confirmed: bool = False
    ) -> dict[str, Any]:
        """Apply an ActionProposal with transaction boundary and revision check."""
        action_type = proposal_dict.get("action_type")
        params = proposal_dict.get("parameters", {})
        requires_confirmation = proposal_dict.get("requires_confirmation", False)

        if requires_confirmation and not confirmed:
            return {
                "status": "confirmation_required",
                "message": "This operation requires explicit user confirmation before execution.",
                "proposal": proposal_dict,
            }

        try:
            if action_type == "create_task":
                due_dt = None
                if params.get("due_date"):
                    due_dt, _ = resolve_date_expression(params["due_date"], self.ref_time)
                task = await self.task_service.create_task(
                    owner_id=self.owner_id,
                    title=params["title"],
                    project_id=params.get("project_id"),
                    description=params.get("description"),
                    priority=params.get("priority", 3),
                    due_date=due_dt,
                    extraction_meta={"source": "assistant_nova"},
                )
                return {
                    "status": "applied",
                    "action_type": "create_task",
                    "task_id": task.id,
                    "title": task.title,
                    "revision": task.revision,
                    "message": f"Created task **{task.title}** (ID: `{task.id}`)",
                }

            elif action_type == "complete_task":
                task_id = params["task_id"]
                rev = params.get("expected_revision")
                task = await self.task_service.complete_task(
                    self.owner_id, task_id, expected_revision=rev
                )
                return {
                    "status": "applied",
                    "action_type": "complete_task",
                    "task_id": task.id,
                    "revision": task.revision,
                    "message": f"Completed task **{task.title}** (ID: `{task.id}`)",
                }

            elif action_type == "reschedule_task":
                task_id = params["task_id"]
                rev = params.get("expected_revision")
                due_dt, _ = resolve_date_expression(params["due_date"], self.ref_time)
                task = await self.task_service.update_task(
                    self.owner_id, task_id, expected_revision=rev, due_date=due_dt
                )
                due_str = task.due_date.strftime("%Y-%m-%d %H:%M UTC") if task.due_date else "None"
                return {
                    "status": "applied",
                    "action_type": "reschedule_task",
                    "task_id": task.id,
                    "revision": task.revision,
                    "due_date": due_str,
                    "message": f"Rescheduled task **{task.title}** to {due_str}",
                }

            elif action_type == "update_task":
                task_id = params["task_id"]
                rev = params.get("expected_revision")
                update_fields: dict[str, Any] = {}
                for k in ("title", "description", "priority", "status", "project_id"):
                    if k in params and params[k] is not None:
                        update_fields[k] = params[k]
                if params.get("due_date"):
                    due_dt, _ = resolve_date_expression(params["due_date"], self.ref_time)
                    update_fields["due_date"] = due_dt

                task = await self.task_service.update_task(
                    self.owner_id, task_id, expected_revision=rev, **update_fields
                )
                return {
                    "status": "applied",
                    "action_type": "update_task",
                    "task_id": task.id,
                    "revision": task.revision,
                    "message": f"Updated task **{task.title}** (ID: `{task.id}`)",
                }

            elif action_type == "delete_task":
                task_id = params["task_id"]
                rev = params.get("expected_revision")
                await self.task_service.delete_task(self.owner_id, task_id, expected_revision=rev)
                return {
                    "status": "applied",
                    "action_type": "delete_task",
                    "task_id": task_id,
                    "message": f"Deleted task `{task_id}`",
                }

            return {"status": "error", "message": f"Unknown action_type '{action_type}'"}

        except ServiceConflictError as exc:
            return {
                "status": "conflict",
                "message": f"Conflict applying action: {exc}",
                "current_revision": exc.current_revision,
            }
        except ServiceNotFound as exc:
            return {"status": "not_found", "message": f"Entity not found: {exc}"}
        except Exception as exc:
            log.error("Failed to apply proposal: %s", exc, exc_info=True)
            return {"status": "error", "message": f"Failed to execute proposal: {exc}"}
