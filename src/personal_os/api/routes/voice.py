"""FastAPI router for LiveKit voice token and session orchestration."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from personal_os.api.deps import get_current_user, get_raphael_service
from personal_os.config import Settings, get_settings
from personal_os.db.models import User
from personal_os.services.raphael import RaphaelService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/voice", tags=["Voice"])


class VoiceTokenRequest(BaseModel):
    """Optional payload to request a room token for a specific conversation."""

    conversation_id: str | None = Field(
        default=None,
        max_length=128,
        description="Optional existing conversation ID. If omitted, uses active conversation.",
    )


class VoiceTokenResponse(BaseModel):
    """LiveKit room connection credentials scoped to the authenticated user."""

    livekit_url: str
    livekit_token: str
    conversation_id: str
    room_name: str
    expires_in: int


class VoiceHealthResponse(BaseModel):
    """Health and provider status for LiveKit voice services."""

    status: str
    livekit_configured: bool
    environment: str


@router.get("/health", response_model=VoiceHealthResponse)
async def get_voice_health(
    settings: Annotated[Settings, Depends(get_settings)],
) -> VoiceHealthResponse:
    """Check voice service health and provider readiness."""
    configured = bool(
        settings.livekit_url
        and (
            settings.environment in ("local", "test")
            or (settings.livekit_api_key and settings.livekit_api_secret)
        )
    )
    return VoiceHealthResponse(
        status="ok" if configured else "unconfigured",
        livekit_configured=configured,
        environment=settings.environment,
    )


@router.post("/token", response_model=VoiceTokenResponse)
async def create_voice_token(
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RaphaelService, Depends(get_raphael_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    payload: VoiceTokenRequest | None = None,
) -> VoiceTokenResponse:
    """Issue a scoped, short-lived LiveKit access token for real-time voice chat with Raphael.

    1. Validates or resolves the active Raphael conversation for the authenticated user.
    2. Enforces cross-tenant isolation (404 if conversation belongs to another user).
    3. Issues a short-lived internal session delegation token for the worker.
    4. Generates a signed LiveKit room token with minimum required permissions.
    """
    conversation_id = payload.conversation_id if payload else None

    if conversation_id:
        # Enforces ownership: raises ServiceNotFound -> 404 if not found or cross-tenant
        conversation = await service.get_conversation(
            owner_id=current_user.id,
            conversation_id=conversation_id,
        )
    else:
        conversation = await service.get_or_create_active_conversation(owner_id=current_user.id)

    room_name = f"pos-voice-{conversation.id}"

    # Determine LiveKit credentials based on environment
    api_key = settings.livekit_api_key or ("devkey" if settings.environment in ("local", "test") else None)
    api_secret = settings.livekit_api_secret or ("secret" if settings.environment in ("local", "test") else None)

    if not api_key or not api_secret:
        log.error("LiveKit credentials not configured in %s environment", settings.environment)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LiveKit server credentials are not configured",
        )

    # Delegation secret used to sign worker session tokens
    delegation_secret = (
        settings.companion_jwt_secret
        or settings.livekit_api_secret
        or ("dev-delegation-secret" if settings.environment in ("local", "test") else None)
    )
    if not delegation_secret:
        delegation_secret = api_secret

    ttl = timedelta(seconds=settings.livekit_token_ttl_seconds)
    now = datetime.now(UTC)
    exp = now + ttl

    # Internal session delegation token for the worker to call Raphael API on behalf of this user
    delegation_payload = {
        "sub": current_user.cognito_sub,
        "user_id": current_user.id,
        "email": current_user.email,
        "conversation_id": conversation.id,
        "room_name": room_name,
        "token_use": "voice_delegation",
        "iss": "personal-os-api",
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    worker_token = jwt.encode(delegation_payload, delegation_secret, algorithm="HS256")

    # Room metadata consumed by the LiveKit agent worker
    room_metadata = json.dumps({
        "user_id": current_user.id,
        "conversation_id": conversation.id,
        "worker_token": worker_token,
    })

    try:
        from livekit.api import AccessToken, VideoGrants

        token = (
            AccessToken(api_key, api_secret)
            .with_identity(f"user-{current_user.id}")
            .with_name(current_user.email or f"User-{current_user.id[:8]}")
            .with_grants(
                VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )
            .with_metadata(room_metadata)
            .with_ttl(ttl)
            .to_jwt()
        )
    except Exception as exc:
        log.error("Failed to generate LiveKit room access token: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate voice session token",
        ) from exc

    log.info(
        "Issued LiveKit token for user %s, conversation %s, room %s (ttl=%ds)",
        current_user.id,
        conversation.id,
        room_name,
        settings.livekit_token_ttl_seconds,
    )

    return VoiceTokenResponse(
        livekit_url=settings.livekit_url,
        livekit_token=token,
        conversation_id=conversation.id,
        room_name=room_name,
        expires_in=settings.livekit_token_ttl_seconds,
    )
