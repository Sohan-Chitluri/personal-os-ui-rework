"""Service adapter layer for M1-C API.

Provides non-conflicting query adapters for read operations (such as capture
processing status) that are not exposed directly on TaskService or CaptureService,
without mutating the shared M1-B interfaces or service files.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.api.schemas import CaptureResponse
from personal_os.db.models import Capture, CaptureProcessing
from personal_os.db.repositories.sqlalchemy import (
    SqlCaptureProcessingRepository,
    SqlCaptureRepository,
)
from personal_os.services.interfaces import ServiceNotFound


class ProcessingStatusService:
    """Read-only adapter for capture processing status and job information."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.processing = SqlCaptureProcessingRepository(session)
        self.captures = SqlCaptureRepository(session)

    async def get_status_by_capture(
        self, owner_id: str, capture_id: str
    ) -> CaptureProcessing | None:
        """Get processing status for a capture, strictly scoped to owner_id."""
        capture = await self.captures.get(capture_id, owner_id)
        if capture is None:
            raise ServiceNotFound("capture", capture_id)
        return await self.processing.get_by_capture(capture_id)

    async def get_status_by_job(self, owner_id: str, job_id: str) -> CaptureProcessing:
        """Get processing job by job_id, verifying the capture belongs to owner_id."""
        stmt = select(CaptureProcessing).where(CaptureProcessing.id == job_id)
        job = (await self.session.execute(stmt)).scalar_one_or_none()
        if job is None:
            raise ServiceNotFound("processing_job", job_id)

        # Enforce owner scoping via the linked capture record
        capture = await self.captures.get(job.capture_id, owner_id)
        if capture is None:
            raise ServiceNotFound("processing_job", job_id)

        return job

    async def enrich_capture(self, owner_id: str, capture: Capture) -> CaptureResponse:
        """Convert a Capture ORM model into a CaptureResponse enriched with processing details."""
        job = await self.processing.get_by_capture(capture.id)
        return CaptureResponse(
            id=capture.id,
            owner_id=capture.owner_id,
            project_id=capture.project_id,
            content=capture.content,
            source=capture.source,
            content_type=capture.content_type,
            status=capture.status,
            idempotency_key=capture.idempotency_key,
            revision=capture.revision,
            created_at=capture.created_at,
            updated_at=capture.updated_at,
            processing_status=job.status if job else capture.status,
            processing_job_id=job.id if job else None,
            task_ids=list(job.task_ids) if (job and job.task_ids) else [],
            extraction_error=job.error if job else None,
        )

    async def enrich_captures(
        self, owner_id: str, captures: Sequence[Capture]
    ) -> list[CaptureResponse]:
        """Enrich a sequence of captures with their processing statuses."""
        if not captures:
            return []
        capture_ids = [c.id for c in captures]
        stmt = select(CaptureProcessing).where(CaptureProcessing.capture_id.in_(capture_ids))
        jobs = (await self.session.execute(stmt)).scalars().all()
        job_map = {j.capture_id: j for j in jobs}

        results = []
        for c in captures:
            j = job_map.get(c.id)
            results.append(
                CaptureResponse(
                    id=c.id,
                    owner_id=c.owner_id,
                    project_id=c.project_id,
                    content=c.content,
                    source=c.source,
                    content_type=c.content_type,
                    status=c.status,
                    idempotency_key=c.idempotency_key,
                    revision=c.revision,
                    created_at=c.created_at,
                    updated_at=c.updated_at,
                    processing_status=j.status if j else c.status,
                    processing_job_id=j.id if j else None,
                    task_ids=list(j.task_ids) if (j and j.task_ids) else [],
                    extraction_error=j.error if j else None,
                )
            )
        return results


__all__ = ["ProcessingStatusService"]
