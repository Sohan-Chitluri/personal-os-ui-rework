"""FastAPI application factory and middleware configuration for Personal OS (M1-C)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from personal_os.api.companion_broker import PostgresNotificationBus, get_companion_broker
from personal_os.api.routes import (
    captures_router,
    companion_ws_router,
    devices_router,
    github_router,
    health_router,
    processing_router,
    projects_router,
    raphael_router,
    sync_router,
    tasks_router,
)
from personal_os.config import Settings, get_settings
from personal_os.db.session import adispose_engine, build_engine, build_session_factory
from personal_os.services.interfaces import ServiceConflictError, ServiceNotFound
from personal_os.services.queue import build_capture_queue

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    s = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if s.environment in ("staging", "production") and not (
            s.cognito_user_pool_id and s.cognito_app_client_id
        ):
            raise RuntimeError("Cognito pool and app client are required outside local/test")
        if s.environment in ("staging", "production") and not s.companion_jwt_secret:
            raise RuntimeError("COMPANION_JWT_SECRET is required outside local/test")
        app.state.capture_queue = build_capture_queue(s)
        try:
            engine = build_engine(s)
            app.state.db_session_factory = build_session_factory(engine)
            if str(s.database_url).startswith("postgresql"):
                app.state.companion_broker.bus = PostgresNotificationBus(
                    str(s.database_url), session_factory=app.state.db_session_factory
                )
                app.state.companion_broker.is_cluster_mode = True
            await app.state.companion_broker.start_coordinator(app.state.db_session_factory)
            log.info("Personal OS API initialized in environment: %s", s.environment)
            yield
        finally:
            if hasattr(app.state, "companion_broker"):
                try:
                    await app.state.companion_broker.stop_coordinator()
                except Exception as exc:
                    log.warning("Failed to stop companion broker cleanly: %s", exc)
            queue = app.state.capture_queue
            close = getattr(queue, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
            app.state.capture_queue = None
            app.state.db_session_factory = None
            await adispose_engine()
            log.info("Personal OS API resources disposed")

    app = FastAPI(
        title="Personal OS API",
        version="0.1.0",
        description="Personal OS Cloud Control Plane API (Milestone 1 — Checkpoint 1)",
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    app.state.settings = s
    app.state.companion_broker = get_companion_broker()

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ----------------------------------------------------------------------
    # Exception Handlers — RFC 7807 Problem Details
    # ----------------------------------------------------------------------

    @app.exception_handler(ServiceNotFound)
    async def service_not_found_handler(request: Request, exc: ServiceNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "type": "https://api.pos-deku.com/errors/not_found",
                "title": "Resource not found",
                "status": status.HTTP_404_NOT_FOUND,
                "detail": str(exc),
                "instance": request.url.path,
                "extensions": {},
            },
            media_type="application/problem+json",
        )

    @app.exception_handler(ServiceConflictError)
    async def service_conflict_handler(request: Request, exc: ServiceConflictError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "type": "https://api.pos-deku.com/errors/conflict",
                "title": "Resource conflict",
                "status": status.HTTP_409_CONFLICT,
                "detail": str(exc),
                "instance": request.url.path,
                "extensions": {
                    "entity": exc.entity,
                    "entity_id": exc.entity_id,
                    "current_revision": exc.current_revision,
                },
            },
            media_type="application/problem+json",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "type": "https://api.pos-deku.com/errors/validation_error",
                "title": "Validation Error",
                "status": 422,
                "detail": "Request body or query parameter validation failed",
                "instance": request.url.path,
                "extensions": {
                    "errors": exc.errors(),
                },
            },
            media_type="application/problem+json",
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        headers = getattr(exc, "headers", None) or {}
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "type": f"https://api.pos-deku.com/errors/http_{exc.status_code}",
                "title": "HTTP Error",
                "status": exc.status_code,
                "detail": exc.detail if isinstance(exc.detail, str) else str(exc.detail),
                "instance": request.url.path,
                "extensions": {},
            },
            headers=headers,
            media_type="application/problem+json",
        )

    # ----------------------------------------------------------------------
    # Route Registration
    # ----------------------------------------------------------------------

    # Root and api/v1 health endpoints
    app.include_router(health_router)
    app.include_router(health_router, prefix="/api/v1")

    # Domain routers
    app.include_router(projects_router)
    app.include_router(tasks_router)
    app.include_router(sync_router)
    app.include_router(captures_router)
    app.include_router(processing_router)
    app.include_router(devices_router)
    app.include_router(companion_ws_router)
    app.include_router(raphael_router)
    app.include_router(github_router)

    return app


# Default ASGI application instance for uvicorn
app = create_app()

__all__ = ["create_app", "app"]
