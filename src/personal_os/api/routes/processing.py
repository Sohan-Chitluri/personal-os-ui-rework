"""Processing jobs API endpoints (M1-C)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from personal_os.api.adapters import ProcessingStatusService
from personal_os.api.deps import (
    get_current_user,
    get_processing_status_service,
)
from personal_os.api.schemas import ProcessingJobStatus
from personal_os.db.models import User

router = APIRouter(prefix="/api/v1/processing", tags=["Processing"])


@router.get(
    "/{job_id}",
    response_model=ProcessingJobStatus,
    summary="Get processing job status",
    description="Retrieve processing status for a job (strictly scoped to user's capture).",
)
async def get_processing_job_status(
    job_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    status_service: Annotated[ProcessingStatusService, Depends(get_processing_status_service)],
) -> ProcessingJobStatus:
    job = await status_service.get_status_by_job(owner_id=current_user.id, job_id=job_id)
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
