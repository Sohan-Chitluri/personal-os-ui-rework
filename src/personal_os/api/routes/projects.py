"""Projects API endpoints (M1-C)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from personal_os.api.deps import (
    get_current_user,
    get_project_service,
    parse_if_match_header,
)
from personal_os.api.schemas import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
)
from personal_os.db.models import User
from personal_os.db.repositories import StaleRevisionError
from personal_os.services.interfaces import ProjectService, ServiceConflictError

router = APIRouter(prefix="/api/v1/projects", tags=["Projects"])


@router.get(
    "",
    response_model=ProjectListResponse,
    summary="List projects",
    description="List all active/filtered projects owned by the authenticated user.",
)
async def list_projects(
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[ProjectService, Depends(get_project_service)],
    status_filter: Annotated[
        str | None,
        Query(alias="status", description="Filter by status (e.g. 'active')"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500, description="Max projects to return")] = 100,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
) -> ProjectListResponse:
    projects = await service.list_projects(
        owner_id=current_user.id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    if hasattr(service, "count_projects"):
        total = await service.count_projects(
            owner_id=current_user.id,
            status=status_filter,
        )
    elif hasattr(service, "count_for_owner"):
        total = await service.count_for_owner(
            owner_id=current_user.id,
            status=status_filter,
        )
    else:
        total = len(projects)
    items = [ProjectResponse.model_validate(p) for p in projects]
    return ProjectListResponse(items=items, total=total)


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create project",
    description="Create a new project owned by the authenticated user.",
)
async def create_project(
    payload: ProjectCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectResponse:
    project = await service.create_project(
        owner_id=current_user.id,
        name=payload.name,
        description=payload.description,
    )
    return ProjectResponse.model_validate(project)


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Get project",
    description="Retrieve a single project by ID (strictly owner-scoped).",
)
async def get_project(
    project_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectResponse:
    project = await service.get_project(owner_id=current_user.id, project_id=project_id)
    return ProjectResponse.model_validate(project)


@router.patch(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Update project",
    description="Update project fields with optimistic locking via optional If-Match header.",
)
async def update_project(
    project_id: str,
    payload: ProjectUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[ProjectService, Depends(get_project_service)],
    expected_revision: Annotated[int | None, Depends(parse_if_match_header)] = None,
) -> ProjectResponse:
    fields = payload.model_dump(exclude_unset=True)
    project = await service.update_project(
        owner_id=current_user.id,
        project_id=project_id,
        expected_revision=expected_revision,
        **fields,
    )
    return ProjectResponse.model_validate(project)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete project",
    description=(
        "Soft-delete a project owned by the authenticated user "
        "with optional optimistic locking via If-Match."
    ),
)
async def delete_project(
    project_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[ProjectService, Depends(get_project_service)],
    expected_revision: Annotated[int | None, Depends(parse_if_match_header)] = None,
) -> None:
    try:
        await service.delete_project(
            owner_id=current_user.id,
            project_id=project_id,
            expected_revision=expected_revision,
        )
    except (ServiceConflictError, StaleRevisionError) as exc:
        current_rev = getattr(exc, "current_revision", None)
        detail = (
            f"Project {project_id} revision mismatch: current revision is {current_rev}"
            if current_rev is not None
            else f"Project {project_id} precondition failed"
        )
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail=detail,
        ) from exc
