"""Authenticated v1 synchronization upload and change-feed endpoints."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from personal_os.api.deps import get_current_user, get_sync_store
from personal_os.api.schemas_sync import (
    SyncChangePageResponse,
    SyncUploadRequest,
    SyncUploadResponse,
)
from personal_os.db.models import User
from personal_os.sync.contracts import PROTOCOL_VERSION, ConflictRecord, SyncOperation
from personal_os.sync.postgres_store import (
    ExpiredSyncCursorError,
    InvalidSyncCursorError,
    PostgresSyncStore,
)

router = APIRouter(prefix="/api/v1/sync", tags=["Synchronization"])
_CURSOR_TTL = timedelta(hours=24)


def _problem(
    request: Request, *, status_code: int, code: str, title: str, detail: str
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content={
            "type": f"https://api.pos-deku.com/errors/sync/{code}",
            "title": title,
            "status": status_code,
            "detail": detail,
            "instance": request.url.path,
            "extensions": {"code": code},
        },
    )


@router.post(
    "/operations",
    response_model=SyncUploadResponse,
    summary="Upload synchronization operations",
    responses={
        401: {"description": "Missing or invalid bearer token"},
        409: {"description": "Unsupported protocol version"},
        422: {"description": "Malformed or unsupported operation envelope"},
        503: {"description": "PostgreSQL synchronization store unavailable"},
    },
)
async def upload_operations(
    payload: SyncUploadRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    store: Annotated[PostgresSyncStore, Depends(get_sync_store)],
) -> SyncUploadResponse | JSONResponse:
    """Apply a bounded batch; each operation has an independent durable result."""
    if payload.protocol_version != PROTOCOL_VERSION:
        return _problem(
            request,
            status_code=status.HTTP_409_CONFLICT,
            code="protocol_mismatch",
            title="Unsupported synchronization protocol",
            detail=f"This server supports protocol version {PROTOCOL_VERSION}.",
        )

    operations: list[SyncOperation] = []
    for raw in payload.operations:
        if "protocol_version" not in raw:
            return _problem(
                request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="invalid_operation",
                title="Invalid synchronization operation",
                detail="Every operation envelope must include protocol_version.",
            )
        raw_version = raw.get("protocol_version")
        if raw_version != PROTOCOL_VERSION:
            return _problem(
                request,
                status_code=status.HTTP_409_CONFLICT,
                code="protocol_mismatch",
                title="Unsupported synchronization protocol",
                detail=f"Every operation must use protocol version {PROTOCOL_VERSION}.",
            )
        try:
            operations.append(SyncOperation.model_validate(raw))
        except ValidationError as exc:
            errors = [
                {key: error[key] for key in ("loc", "type", "msg") if key in error}
                for error in exc.errors(include_input=False)
            ]
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                media_type="application/problem+json",
                content={
                    "type": "https://api.pos-deku.com/errors/sync/invalid_operation",
                    "title": "Invalid synchronization operation",
                    "status": status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "detail": "One or more operation envelopes are malformed or unsupported.",
                    "instance": request.url.path,
                    "extensions": {"code": "invalid_operation", "validation_errors": errors},
                },
            )

    acknowledgements = []
    for operation in operations:
        acknowledgements.append(await store.apply_operation(current_user.id, operation))
    return SyncUploadResponse(
        protocol_version=PROTOCOL_VERSION,
        acknowledgements=acknowledgements,
    )


@router.get(
    "/changes",
    response_model=SyncChangePageResponse,
    summary="Read the authenticated change feed",
    description=(
        "Pages are ordered by PostgreSQL feed sequence. Apply every item durably "
        "before saving next_cursor; on expiry or invalid cursor, restart without "
        "a cursor and replay the retained feed from the beginning."
    ),
    responses={
        400: {"description": "Malformed or unknown cursor; replay from the beginning"},
        401: {"description": "Missing or invalid bearer token"},
        410: {"description": "Expired cursor; replay from the beginning"},
        503: {"description": "PostgreSQL synchronization store unavailable"},
    },
)
async def get_changes(
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    store: Annotated[PostgresSyncStore, Depends(get_sync_store)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> SyncChangePageResponse | JSONResponse:
    try:
        page = await store.get_change_page(
            current_user.id,
            cursor=cursor,
            limit=limit,
            cursor_ttl=_CURSOR_TTL,
        )
    except ExpiredSyncCursorError:
        return _problem(
            request,
            status_code=status.HTTP_410_GONE,
            code="cursor_expired",
            title="Synchronization cursor expired",
            detail=(
                "Discard this cursor and request /api/v1/sync/changes without a cursor "
                "to replay the retained feed from sequence zero."
            ),
        )
    except InvalidSyncCursorError:
        return _problem(
            request,
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_cursor",
            title="Invalid synchronization cursor",
            detail=(
                "This cursor is unknown or not valid for this account. Request "
                "/api/v1/sync/changes without a cursor to replay the retained feed."
            ),
        )
    return SyncChangePageResponse(
        protocol_version=PROTOCOL_VERSION,
        items=list(page.items),
        next_cursor=page.next_cursor,
        cursor_expires_at=page.cursor_expires_at,
        has_more=page.has_more,
    )


@router.get(
    "/conflicts/{conflict_id}",
    response_model=ConflictRecord,
    summary="Inspect an owner-scoped synchronization conflict",
    responses={
        401: {"description": "Missing or invalid bearer token"},
        404: {"description": "Conflict does not exist for this account"},
    },
)
async def get_conflict(
    conflict_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    store: Annotated[PostgresSyncStore, Depends(get_sync_store)],
) -> ConflictRecord | JSONResponse:
    conflict = await store.get_conflict(current_user.id, conflict_id)
    if conflict is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            media_type="application/problem+json",
            content={
                "type": "https://api.pos-deku.com/errors/sync/conflict_not_found",
                "title": "Synchronization conflict not found",
                "status": status.HTTP_404_NOT_FOUND,
                "detail": "No conflict with this ID is available for this account.",
                "instance": f"/api/v1/sync/conflicts/{conflict_id}",
                "extensions": {"code": "conflict_not_found"},
            },
        )
    return conflict
