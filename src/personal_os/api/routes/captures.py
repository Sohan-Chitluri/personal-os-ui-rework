"""Captures API endpoints (M1-C)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response, status

from personal_os.api.adapters import ProcessingStatusService
from personal_os.api.deps import (
    get_capture_service,
    get_current_user,
    get_processing_status_service,
)
from personal_os.api.schemas import (
    CaptureCreate,
    CaptureListResponse,
    CaptureReceipt,
    CaptureResponse,
    ProcessingJobStatus,
)
from personal_os.db.models import User
from personal_os.services.interfaces import (
    CaptureService,
    QueueDispatchError,
    ServiceNotFound,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/captures", tags=["Captures"])


@router.post(
    "",
    response_model=CaptureReceipt,
    status_code=status.HTTP_201_CREATED,
    summary="Submit capture",
    description="Submit a new raw capture with persist-first durability.",
)
async def submit_capture(
    payload: CaptureCreate,
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[CaptureService, Depends(get_capture_service)],
    idempotency_key_header: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
) -> CaptureReceipt:
    idem_key = payload.idempotency_key or idempotency_key_header

    try:
        capture = await service.submit_capture(
            owner_id=current_user.id,
            content=payload.content,  # type: ignore[arg-type]
            source=payload.source,
            content_type=payload.content_type,
            project_id=payload.project_id,
            idempotency_key=idem_key,
        )
        return CaptureReceipt(
            capture_id=capture.id,
            persisted=True,
            timestamp=capture.created_at,
            status=capture.status,
            error=None,
        )
    except QueueDispatchError as exc:
        log.warning("Capture persisted but queue dispatch failed: %s", exc)
        response.status_code = status.HTTP_202_ACCEPTED
        return CaptureReceipt(
            capture_id=exc.capture_id or "pending-recovery",
            persisted=True,
            timestamp=datetime.now(UTC),
            status="pending",
            error=f"Persisted to database; queue dispatch failed, queued for recovery: {exc}",
        )


@router.get(
    "",
    response_model=CaptureListResponse,
    summary="List captures",
    description="List recent captures for the authenticated user, enriched with status.",
)
async def list_captures(
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[CaptureService, Depends(get_capture_service)],
    status_service: Annotated[ProcessingStatusService, Depends(get_processing_status_service)],
    status_filter: Annotated[
        str | None,
        Query(alias="status", description="Filter by capture status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500, description="Max items to return")] = 100,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
) -> CaptureListResponse:
    captures = await service.list_captures(
        owner_id=current_user.id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    if hasattr(service, "count_captures"):
        total = await service.count_captures(
            owner_id=current_user.id,
            status=status_filter,
        )
    elif hasattr(service, "count_for_owner"):
        total = await service.count_for_owner(
            owner_id=current_user.id,
            status=status_filter,
        )
    else:
        total = len(captures)
    items = await status_service.enrich_captures(current_user.id, captures)
    return CaptureListResponse(items=items, total=total)


@router.get(
    "/{capture_id}",
    response_model=CaptureResponse,
    summary="Get capture",
    description="Retrieve a single capture by ID, enriched with its processing status.",
)
async def get_capture(
    capture_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[CaptureService, Depends(get_capture_service)],
    status_service: Annotated[ProcessingStatusService, Depends(get_processing_status_service)],
) -> CaptureResponse:
    capture = await service.get_capture(owner_id=current_user.id, capture_id=capture_id)
    return await status_service.enrich_capture(current_user.id, capture)


@router.get(
    "/{capture_id}/status",
    response_model=ProcessingJobStatus,
    summary="Get capture processing status",
    description="Retrieve the processing pipeline job record for a specific capture.",
)
async def get_capture_processing_status(
    capture_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    status_service: Annotated[ProcessingStatusService, Depends(get_processing_status_service)],
) -> ProcessingJobStatus:
    job = await status_service.get_status_by_capture(
        owner_id=current_user.id,
        capture_id=capture_id,
    )
    if job is None:
        raise ServiceNotFound("capture_processing", capture_id)
    return ProcessingJobStatus(
        id=job.id,
        capture_id=job.capture_id,
        status=job.status,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        error=job.error,
        started_at=job.started_at,
        finished_at=job.finished_at,
        task_ids=list(job.task_ids) if job.task_ids else [],
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.delete(
    "/{capture_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete capture",
    description="Soft-delete a capture owned by the authenticated user.",
)
async def delete_capture(
    capture_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[CaptureService, Depends(get_capture_service)],
) -> None:
    await service.delete_capture(owner_id=current_user.id, capture_id=capture_id)
