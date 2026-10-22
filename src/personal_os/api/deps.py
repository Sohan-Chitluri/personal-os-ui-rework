"""FastAPI dependency injection for database sessions, services, auth, and queue.

Per consolidated architecture:
- Thin HTTP handlers delegate to domain services via dependency injection.
- Database sessions are scoped to requests (commit on 2xx, rollback on error).
- Authentication enforces user ownership at the boundary; request bodies cannot
  override or supply owner_id.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Annotated, cast

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.api.adapters import ProcessingStatusService
from personal_os.api.companion_broker import CompanionBroker, get_companion_broker
from personal_os.config import Settings, get_settings
from personal_os.db.models import Device, User
from personal_os.db.repositories.sqlalchemy import SqlUserRepository
from personal_os.services.devices import DeviceAuthError, DeviceService, build_device_service
from personal_os.services.hermes import HermesClient, build_hermes_client
from personal_os.services.interfaces import (
    CaptureQueue,
    CaptureService,
    ProjectService,
    TaskService,
)
from personal_os.services.raphael import RaphaelService
from personal_os.services.tasks import (
    CaptureServiceImpl,
    ProjectServiceImpl,
    TaskServiceImpl,
)
from personal_os.sync.postgres_store import PostgresSyncStore

log = logging.getLogger(__name__)

# Reusable security scheme (Bearer token)
bearer_scheme = HTTPBearer(auto_error=False)

_jwks_clients: dict[str, PyJWKClient] = {}


def get_capture_queue(request: Request) -> CaptureQueue:
    """Return the one queue instance created by the application lifespan."""
    queue = getattr(request.app.state, "capture_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="Capture queue is unavailable")
    return cast(CaptureQueue, queue)


def get_sync_store(request: Request) -> PostgresSyncStore:
    """Resolve the PostgreSQL sync adapter from the active app session factory."""
    factory = getattr(request.app.state, "db_session_factory", None)
    if factory is None:
        raise HTTPException(status_code=503, detail="Database is unavailable")
    try:
        return PostgresSyncStore(factory)
    except ValueError as exc:
        log.error("Synchronization requires PostgreSQL persistence")
        raise HTTPException(
            status_code=503, detail="Synchronization database is unavailable"
        ) from exc


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Provide a request session bound to the settings used by this app factory."""
    factory = getattr(request.app.state, "db_session_factory", None)
    if factory is None:
        raise HTTPException(status_code=503, detail="Database is unavailable")
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def verify_cognito_token(token: str, settings: Settings) -> dict:
    """Verify Cognito signature and token claims using cached rotating JWKS."""
    if not settings.cognito_user_pool_id or not settings.cognito_app_client_id:
        raise ValueError("Cognito issuer and app client must be configured")
    issuer = f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/{settings.cognito_user_pool_id}"
    jwks_url = f"{issuer}/.well-known/jwks.json"
    client = _jwks_clients.setdefault(issuer, PyJWKClient(jwks_url, cache_jwk_set=True))
    key = client.get_signing_key_from_jwt(token).key
    claims = jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        issuer=issuer,
        options={
            "require": ["exp", "iat", "iss", "sub", "token_use"],
            "verify_aud": False,
        },
        leeway=0,
    )
    if claims.get("token_use") != settings.cognito_token_use:
        raise ValueError("Unexpected Cognito token use")
    if settings.cognito_token_use == "id":
        if claims.get("aud") != settings.cognito_app_client_id:
            raise ValueError("Unexpected Cognito audience")
    elif claims.get("client_id") != settings.cognito_app_client_id:
        raise ValueError("Unexpected Cognito client")
    return claims


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    """Authenticate the caller from the Bearer token.

    Resolves only verified Cognito JWT claims outside local/test environments.
    In local/test environments:
    - Accepts static dev token (settings.dev_auth_token)
    - Accepts 'test:<sub_or_name>' or 'user:<sub_or_name>' tokens
    - Never trusts decoded but unsigned claims
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials.strip()
    user_repo = SqlUserRepository(session)

    local_mode = settings.environment in ("local", "test")
    # Synthetic authentication is deliberately impossible outside local/test.
    if local_mode and settings.dev_auth_token and token == settings.dev_auth_token:
        user = await user_repo.get_or_create_by_sub("dev-user", "dev@example.com")
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive"
            )
        return user

    # 2. Local / test synthetic bearer tokens: "test:<name>" or "user:<name>"
    if (
        local_mode
        and settings.allow_synthetic_auth
        and (token.startswith("test:") or token.startswith("user:"))
    ):
        sub = token.split(":", 1)[1].strip()
        if not sub:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid bearer token identifier",
                headers={"WWW-Authenticate": "Bearer"},
            )
        email = f"{sub}@example.com"
        user = await user_repo.get_or_create_by_sub(sub, email)
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive"
            )
        return user

    try:
        claims = await asyncio.to_thread(verify_cognito_token, token, settings)
    except (PyJWTError, ValueError, TypeError) as exc:
        log.warning("Bearer token verification failed (%s)", type(exc).__name__)
        claims = None
    if claims:
        user = await user_repo.get_or_create_by_sub(str(claims["sub"]), claims.get("email"))
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive"
            )
        return user

    # 4. Fallback for non-JWT plain tokens in test/local environments
    if local_mode and settings.allow_synthetic_auth and len(token) >= 3 and "." not in token:
        user = await user_repo.get_or_create_by_sub(token, f"{token}@local.test")
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive"
            )
        return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_task_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TaskService:
    """Dependency injecting TaskService."""
    return TaskServiceImpl(session)


async def get_project_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProjectService:
    """Dependency injecting ProjectService."""
    return ProjectServiceImpl(session)


async def get_capture_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    queue: Annotated[CaptureQueue, Depends(get_capture_queue)],
) -> CaptureService:
    """Dependency injecting CaptureService."""
    return CaptureServiceImpl(session, queue=queue)


async def get_processing_status_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProcessingStatusService:
    """Dependency injecting ProcessingStatusService."""
    return ProcessingStatusService(session)


def parse_if_match_header(
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> int | None:
    """Parse HTTP If-Match header to integer revision for optimistic concurrency.

    Accepts '1', '"1"', 'W/"1"'. Returns None if header omitted or '*'.
    Raises 400 Bad Request on malformed values.
    """
    if not if_match or if_match.strip() == "*":
        return None
    raw = if_match.strip()
    if raw.startswith(('W/"', 'w/"')):
        raw = raw[3:-1]
    elif raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    try:
        return int(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed If-Match revision header: {if_match!r}",
        ) from None


async def get_device_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DeviceService:
    """Dependency injecting DeviceService."""
    try:
        return build_device_service(session, settings)
    except DeviceAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


async def get_current_device(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Device:
    """Authenticate device via Bearer token."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing device authorization bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials.strip()
    try:
        service = build_device_service(session, settings)
    except DeviceAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    try:
        claims = service.verify_device_token(token)
    except DeviceAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    device_id = claims["device_id"]
    user_id = claims["user_id"]
    device = await service.repo.get_by_device_id(device_id, owner_id=user_id)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Device not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if device.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Device has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return device


def get_companion_broker_dep(request: Request) -> CompanionBroker:
    """Dependency injecting the CompanionBroker singleton."""
    return getattr(request.app.state, "companion_broker", None) or get_companion_broker()


def get_hermes_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> HermesClient:
    """Resolve the configured Hermes runtime client."""
    return build_hermes_client(settings)


def get_raphael_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    hermes_client: Annotated[HermesClient, Depends(get_hermes_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RaphaelService:
    """Provide RaphaelService configured with current session and Hermes client."""
    return RaphaelService(session=session, hermes_client=hermes_client, settings=settings)


__all__ = [
    "bearer_scheme",
    "get_db_session",
    "get_current_user",
    "get_current_device",
    "get_device_service",
    "get_companion_broker_dep",
    "get_task_service",
    "get_project_service",
    "get_capture_service",
    "get_processing_status_service",
    "get_capture_queue",
    "get_hermes_client",
    "get_raphael_service",
    "verify_cognito_token",
    "parse_if_match_header",
]
