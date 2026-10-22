"""Health, readiness, and meta endpoints."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.api.deps import get_db_session
from personal_os.api.schemas import HealthResponse, ReadinessResponse, VersionResponse
from personal_os.config import Settings, get_settings

log = logging.getLogger(__name__)

router = APIRouter(tags=["Health & Meta"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    description="Returns 200 OK if the application process is running.",
)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description="Verifies connectivity to database. Returns 503 if not ready.",
)
async def ready(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ReadinessResponse:
    try:
        await session.execute(text("SELECT 1"))
        return ReadinessResponse(status="ready", database="connected")
    except Exception as exc:
        log.warning("Readiness probe database check failed: %s", exc)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status="not_ready",
            database="error",
            details={"error": str(exc)},
        )


@router.get(
    "/version",
    response_model=VersionResponse,
    summary="Version metadata",
    description="Returns API build version and current runtime environment.",
)
async def version(
    settings: Annotated[Settings, Depends(get_settings)],
) -> VersionResponse:
    return VersionResponse(
        version="0.1.0",
        environment=settings.environment,
    )
