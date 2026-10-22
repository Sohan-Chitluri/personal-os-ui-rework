"""Tasks API endpoints (M1-C)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from personal_os.api.deps import (
    get_current_user,
    get_task_service,
    parse_if_match_header,
)
from personal_os.api.schemas import (
    TaskCreate,
    TaskListResponse,
    TaskResponse,
    TaskUpdate,
)
from personal_os.db.models import User
from personal_os.db.repositories import StaleRevisionError
from personal_os.services.interfaces import ServiceConflictError, TaskService

router = APIRouter(prefix="/api/v1/tasks", tags=["Tasks"])


@router.get(
    "",
    response_model=TaskListResponse,
    summary="List tasks",
    description="List tasks owned by the authenticated user with optional filters.",
)
async def list_tasks(
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[TaskService, Depends(get_task_service)],
    project_id: Annotated[str | None, Query(description="Filter by project ID")] = None,
    status_filter: Annotated[
        str | None,
        Query(alias="status", description="Filter by status ('open', 'done')"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500, description="Max tasks to return")] = 100,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
) -> TaskListResponse:
    tasks = await service.list_tasks(
        owner_id=current_user.id,
        project_id=project_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    if hasattr(service, "count_tasks"):
        total = await service.count_tasks(
            owner_id=current_user.id,
            project_id=project_id,
            status=status_filter,
        )
    elif hasattr(service, "count_for_owner"):
        total = await service.count_for_owner(
            owner_id=current_user.id,
            project_id=project_id,
            status=status_filter,
        )
    else:
        total = len(tasks)
    items = [TaskResponse.model_validate(t) for t in tasks]
    return TaskListResponse(items=items, total=total)


@router.post(
    "",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create task",
    description="Create a new task owned by the authenticated user.",
)
async def create_task(
    payload: TaskCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[TaskService, Depends(get_task_service)],
) -> TaskResponse:
    task = await service.create_task(
        owner_id=current_user.id,
        title=payload.title,
        project_id=payload.project_id,
        description=payload.description,
        priority=payload.priority,
        due_date=payload.due_date,
        hard_deadline=payload.hard_deadline,
    )
    return TaskResponse.model_validate(task)


@router.get(
    "/{task_id}",
    response_model=TaskResponse,
    summary="Get task",
    description="Retrieve a single task by ID (strictly owner-scoped).",
)
async def get_task(
    task_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[TaskService, Depends(get_task_service)],
) -> TaskResponse:
    task = await service.get_task(owner_id=current_user.id, task_id=task_id)
    return TaskResponse.model_validate(task)


@router.patch(
    "/{task_id}",
    response_model=TaskResponse,
    summary="Update task",
    description="Update task fields with optimistic locking via optional If-Match header.",
)
async def update_task(
    task_id: str,
    payload: TaskUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[TaskService, Depends(get_task_service)],
    expected_revision: Annotated[int | None, Depends(parse_if_match_header)] = None,
) -> TaskResponse:
    fields = payload.model_dump(exclude_unset=True)
    fields.pop("soft_deadline", None)
    task = await service.update_task(
        owner_id=current_user.id,
        task_id=task_id,
        expected_revision=expected_revision,
        **fields,
    )
    return TaskResponse.model_validate(task)


@router.post(
    "/{task_id}/complete",
    response_model=TaskResponse,
    summary="Complete task",
    description="Mark a task as done with optional revision check.",
)
async def complete_task(
    task_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[TaskService, Depends(get_task_service)],
    expected_revision: Annotated[int | None, Depends(parse_if_match_header)] = None,
) -> TaskResponse:
    task = await service.complete_task(
        owner_id=current_user.id,
        task_id=task_id,
        expected_revision=expected_revision,
    )
    return TaskResponse.model_validate(task)


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete task",
    description=(
        "Soft-delete a task owned by the authenticated user "
        "with optional optimistic locking via If-Match."
    ),
)
async def delete_task(
    task_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[TaskService, Depends(get_task_service)],
    expected_revision: Annotated[int | None, Depends(parse_if_match_header)] = None,
) -> None:
    try:
        await service.delete_task(
            owner_id=current_user.id,
            task_id=task_id,
            expected_revision=expected_revision,
        )
    except (ServiceConflictError, StaleRevisionError) as exc:
        current_rev = getattr(exc, "current_revision", None)
        detail = (
            f"Task {task_id} revision mismatch: current revision is {current_rev}"
            if current_rev is not None
            else f"Task {task_id} precondition failed"
        )
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail=detail,
        ) from exc
