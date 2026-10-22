"""API endpoints for GitHub account authorization, repository discovery, and project association."""

from __future__ import annotations

import logging
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.api.deps import get_current_user, get_db_session
from personal_os.config import Settings, get_settings
from personal_os.db.models import User
from personal_os.services.github import (
    GitHubAuthError,
    GitHubError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubService,
)
from personal_os.services.interfaces import ServiceNotFound

log = logging.getLogger(__name__)

router = APIRouter(tags=["GitHub"])


class GitHubAuthStatusResponse(BaseModel):
    configured: bool = True
    connected: bool = False
    username: str | None = None
    scopes: list[str] | None = None
    updated_at: str | None = None
    token_expired: bool | None = None
    mode: str = "unconfigured"
    public_only: bool = False


class GitHubAuthorizeResponse(BaseModel):
    authorization_url: str
    state: str


class GitHubCallbackRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=512)
    state: str = Field(..., min_length=1, max_length=512)
    redirect_uri: str | None = Field(default=None, max_length=1024)


class GitHubConnectRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=512)
    scopes: str | None = Field(default=None, max_length=255)


class GitHubDisconnectResponse(BaseModel):
    connected: bool


class AssociateRepositoryRequest(BaseModel):
    repository: str = Field(
        ..., min_length=1, max_length=512, description="owner/repo or repo name"
    )


def get_github_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> GitHubService:
    return GitHubService(session=session, settings=settings)


# ---- GitHub Auth Endpoints -------------------------------------------------


@router.get(
    "/api/v1/github/status",
    response_model=GitHubAuthStatusResponse,
    summary="Get GitHub connection status",
)
async def get_github_status(
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[GitHubService, Depends(get_github_service)],
) -> GitHubAuthStatusResponse:
    res = await service.get_auth_status(current_user.id)
    return GitHubAuthStatusResponse(**res)


@router.get(
    "/api/v1/github/authorize",
    response_model=GitHubAuthorizeResponse,
    summary="Initiate GitHub OAuth authorization flow",
)
async def authorize_github(
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[GitHubService, Depends(get_github_service)],
    redirect_uri: Annotated[str | None, Query(description="OAuth redirect callback URI")] = None,
    return_to: Annotated[
        str | None, Query(description="Frontend path to return to after completion")
    ] = None,
) -> GitHubAuthorizeResponse:
    try:
        url, state = await service.create_authorization_url(
            user_id=current_user.id,
            redirect_uri=redirect_uri,
            return_to=return_to,
        )
        return GitHubAuthorizeResponse(authorization_url=url, state=state)
    except GitHubError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get(
    "/api/v1/github/callback",
    summary="GitHub OAuth browser redirect callback",
)
async def github_oauth_callback(
    service: Annotated[GitHubService, Depends(get_github_service)],
    code: Annotated[str | None, Query(description="OAuth authorization code")] = None,
    state: Annotated[str | None, Query(description="OAuth CSRF state parameter")] = None,
    error: Annotated[str | None, Query(description="OAuth error code")] = None,
    error_description: Annotated[str | None, Query(description="OAuth error description")] = None,
) -> RedirectResponse:
    # Handle GitHub OAuth errors or cancellations
    if error:
        err_msg = error_description or error
        return RedirectResponse(
            url=f"/projects?github_error={quote(err_msg)}",
            status_code=status.HTTP_302_FOUND,
        )

    if not code or not state:
        return RedirectResponse(
            url=f"/projects?github_error={quote('Missing code or state parameter in GitHub callback')}",
            status_code=status.HTTP_302_FOUND,
        )

    try:
        res = await service.complete_oauth_callback(code=code, state=state)
        return_to = res.get("return_to") or "/projects"
        sep = "&" if "?" in return_to else "?"
        return RedirectResponse(
            url=f"{return_to}{sep}github_connected=true",
            status_code=status.HTTP_302_FOUND,
        )
    except (GitHubAuthError, GitHubError) as exc:
        return RedirectResponse(
            url=f"/projects?github_error={quote(str(exc))}",
            status_code=status.HTTP_302_FOUND,
        )
    except Exception as exc:
        log.error("Unexpected error in GitHub OAuth callback: %s", exc, exc_info=True)
        return RedirectResponse(
            url=f"/projects?github_error={quote(f'Authorization failed: {exc}')}",
            status_code=status.HTTP_302_FOUND,
        )


@router.post(
    "/api/v1/github/callback",
    response_model=GitHubAuthStatusResponse,
    summary="GitHub OAuth programmatic / SPA callback completion",
)
async def complete_github_oauth(
    payload: GitHubCallbackRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[GitHubService, Depends(get_github_service)],
) -> GitHubAuthStatusResponse:
    try:
        res = await service.complete_oauth_callback(
            code=payload.code,
            state=payload.state,
            user_id=current_user.id,
            redirect_uri=payload.redirect_uri,
        )
        return GitHubAuthStatusResponse(
            configured=True,
            connected=True,
            username=res["username"],
            scopes=res.get("scopes", []),
            mode="authenticated",
            public_only=False,
        )
    except GitHubAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except GitHubError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post(
    "/api/v1/github/connect",
    response_model=GitHubAuthStatusResponse,
    summary="Connect GitHub account with PAT",
)
async def connect_github_account(
    payload: GitHubConnectRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[GitHubService, Depends(get_github_service)],
) -> GitHubAuthStatusResponse:
    try:
        res = await service.connect_token(
            user_id=current_user.id,
            token=payload.token,
            scopes=payload.scopes,
        )
        return GitHubAuthStatusResponse(
            configured=True,
            connected=True,
            username=res["username"],
            scopes=payload.scopes.split(",") if payload.scopes else [],
            mode="authenticated",
            public_only=False,
        )
    except GitHubAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    except GitHubRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        log.error(
            "Failed to connect GitHub account for user %s: %s", current_user.id, exc, exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"GitHub connection failed: {exc}",
        ) from exc


@router.post(
    "/api/v1/github/disconnect",
    response_model=GitHubDisconnectResponse,
    summary="Disconnect GitHub account",
)
async def disconnect_github_account(
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[GitHubService, Depends(get_github_service)],
) -> GitHubDisconnectResponse:
    await service.disconnect(current_user.id)
    return GitHubDisconnectResponse(connected=False)


@router.get(
    "/api/v1/github/repositories",
    response_model=list[dict[str, Any]],
    summary="List or search user repositories",
)
async def list_user_repositories(
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[GitHubService, Depends(get_github_service)],
    q: Annotated[str | None, Query(description="Search query")] = None,
) -> list[dict[str, Any]]:
    try:
        if q:
            return await service.search_repositories(current_user.id, query=q)
        return await service.list_repositories(current_user.id)
    except GitHubAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    except GitHubRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc


# ---- Project Repository Association Endpoints -------------------------------


@router.get(
    "/api/v1/projects/{project_id}/repository",
    summary="Get project repository context",
)
async def get_project_repository_context(
    project_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[GitHubService, Depends(get_github_service)],
) -> dict[str, Any] | None:
    context = await service.get_repository_context(current_user.id, project_id)
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No repository associated with this project",
        )
    return context


@router.post(
    "/api/v1/projects/{project_id}/repository",
    status_code=status.HTTP_201_CREATED,
    summary="Associate a GitHub repository with a project",
)
async def associate_project_repository(
    project_id: str,
    payload: AssociateRepositoryRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[GitHubService, Depends(get_github_service)],
) -> dict[str, Any]:
    try:
        assoc = await service.associate_project_repository(
            owner_id=current_user.id,
            project_id=project_id,
            repo_full_name=payload.repository,
        )
        ctx = await service.get_repository_context(current_user.id, project_id)
        return ctx or {
            "repository": {
                "id": assoc.repo_id,
                "full_name": assoc.full_name,
                "default_branch": assoc.default_branch,
                "html_url": assoc.html_url,
            },
            "project_brief": assoc.project_brief or {},
        }
    except ServiceNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except GitHubNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"GitHub repository '{payload.repository}' not found",
        ) from exc
    except GitHubAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc


@router.post(
    "/api/v1/projects/{project_id}/repository/sync",
    summary="Sync and refresh project repository context",
)
async def sync_project_repository(
    project_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[GitHubService, Depends(get_github_service)],
) -> dict[str, Any]:
    try:
        await service.sync_project_repository(current_user.id, project_id)
        ctx = await service.get_repository_context(current_user.id, project_id)
        if ctx is None:
            raise HTTPException(status_code=404, detail="No repository linked")
        return ctx
    except ServiceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GitHubAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.delete(
    "/api/v1/projects/{project_id}/repository",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect repository from project",
)
async def disconnect_project_repository(
    project_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[GitHubService, Depends(get_github_service)],
) -> None:
    deleted = await service.disconnect_project_repository(current_user.id, project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="No repository associated with this project")
