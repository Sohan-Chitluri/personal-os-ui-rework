"""Tests for LiveKit Voice token vending and session authorization."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient

from personal_os.api.deps import get_current_user, get_raphael_service
from personal_os.api.routes.voice import router as voice_router
from personal_os.config import Settings, get_settings
from personal_os.db.models import Conversation, User
from personal_os.services.interfaces import ServiceNotFound


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        environment="test",
        livekit_url="wss://test.livekit.cloud",
        livekit_api_key="test-livekit-key",
        livekit_api_secret="test-livekit-secret-32-bytes-long!!",
        companion_jwt_secret="test-companion-secret-32-bytes!!",
        livekit_token_ttl_seconds=900,
        allow_synthetic_auth=True,
    )


@pytest.fixture
def test_user() -> User:
    user = User(
        id="01JTESTUSER0000000000000001",
        cognito_sub="sub-user-1",
        email="voice-user@example.com",
        display_name="Voice User",
        is_active=True,
    )
    return user


@pytest.fixture
def app(test_settings: Settings, test_user: User) -> FastAPI:
    application = FastAPI()
    application.include_router(voice_router)

    # Register ServiceNotFound exception handler as in main.py
    @application.exception_handler(ServiceNotFound)
    async def not_found_handler(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    # Mock DB session for dependency injection
    mock_db = AsyncMock()
    async def _mock_db_session():
        yield mock_db

    from personal_os.api.deps import get_db_session
    application.dependency_overrides[get_db_session] = _mock_db_session
    application.dependency_overrides[get_settings] = lambda: test_settings
    return application


@pytest.mark.asyncio
async def test_unauthenticated_request_rejected(app: FastAPI):
    """Voice token endpoint requires authentication."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        res = await client.post("/api/v1/voice/token", json={})
        assert res.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_create_voice_token_for_active_conversation(
    app: FastAPI, test_settings: Settings, test_user: User
):
    """Authenticated user receives scoped LiveKit room credentials for active session."""
    mock_service = AsyncMock()
    mock_conv = Conversation(
        id="01JCONV00000000000000000001",
        owner_id=test_user.id,
        title="Active Raphael Session",
        status="active",
    )
    mock_service.get_or_create_active_conversation.return_value = mock_conv

    app.dependency_overrides[get_current_user] = lambda: test_user
    app.dependency_overrides[get_raphael_service] = lambda: mock_service

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        res = await client.post("/api/v1/voice/token", json={})
        assert res.status_code == 200
        data = res.json()

        assert data["livekit_url"] == test_settings.livekit_url
        assert data["conversation_id"] == mock_conv.id
        assert data["room_name"] == f"pos-voice-{mock_conv.id}"
        assert data["expires_in"] == test_settings.livekit_token_ttl_seconds

        # Decode the LiveKit JWT to verify claims and permissions
        token = data["livekit_token"]
        claims = jwt.decode(
            token,
            test_settings.livekit_api_secret,
            algorithms=["HS256"],
            options={"verify_signature": True},
        )
        assert claims["sub"] == f"user-{test_user.id}"
        assert claims["iss"] == test_settings.livekit_api_key
        assert claims["video"]["room"] == f"pos-voice-{mock_conv.id}"
        assert claims["video"]["roomJoin"] is True
        assert claims["video"]["canPublish"] is True
        assert claims["video"]["canSubscribe"] is True

        # Verify room metadata contains the worker session delegation token
        metadata = json.loads(claims["metadata"])
        assert metadata["user_id"] == test_user.id
        assert metadata["conversation_id"] == mock_conv.id
        assert "worker_token" in metadata

        # Verify worker delegation token
        worker_token = metadata["worker_token"]
        worker_claims = jwt.decode(
            worker_token,
            test_settings.companion_jwt_secret,
            algorithms=["HS256"],
            options={"verify_signature": True},
        )
        assert worker_claims["sub"] == test_user.cognito_sub
        assert worker_claims["user_id"] == test_user.id
        assert worker_claims["conversation_id"] == mock_conv.id
        assert worker_claims["token_use"] == "voice_delegation"


@pytest.mark.asyncio
async def test_create_voice_token_for_specific_conversation(
    app: FastAPI, test_settings: Settings, test_user: User
):
    """User can request a token for a specific conversation ID."""
    conv_id = "01JCONV00000000000000000002"
    mock_service = AsyncMock()
    mock_conv = Conversation(
        id=conv_id,
        owner_id=test_user.id,
        title="Specific Conversation",
        status="active",
    )
    mock_service.get_conversation.return_value = mock_conv

    app.dependency_overrides[get_current_user] = lambda: test_user
    app.dependency_overrides[get_raphael_service] = lambda: mock_service

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        res = await client.post(
            "/api/v1/voice/token", json={"conversation_id": conv_id}
        )
        assert res.status_code == 200
        data = res.json()
        assert data["conversation_id"] == conv_id
        assert data["room_name"] == f"pos-voice-{conv_id}"
        mock_service.get_conversation.assert_awaited_once_with(
            owner_id=test_user.id, conversation_id=conv_id
        )


@pytest.mark.asyncio
async def test_cross_tenant_conversation_rejected(
    app: FastAPI, test_settings: Settings, test_user: User
):
    """Requesting a token for a conversation belonging to another tenant returns 404."""
    attacker_conv_id = "01JCONV00000000000OTHERUSER"
    mock_service = AsyncMock()
    mock_service.get_conversation.side_effect = ServiceNotFound(
        f"Conversation {attacker_conv_id} not found"
    )

    app.dependency_overrides[get_current_user] = lambda: test_user
    app.dependency_overrides[get_raphael_service] = lambda: mock_service

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        res = await client.post(
            "/api/v1/voice/token", json={"conversation_id": attacker_conv_id}
        )
        assert res.status_code == 404


@pytest.mark.asyncio
async def test_voice_delegation_token_authenticates_as_delegated_user(
    app: FastAPI, test_settings: Settings, test_user: User
):
    """Voice delegation JWT enables the worker to authenticate as the delegated user."""
    from fastapi.security import HTTPAuthorizationCredentials
    from personal_os.api.deps import get_current_user

    ttl = timedelta(minutes=15)
    now = datetime.now(UTC)
    payload = {
        "sub": test_user.cognito_sub,
        "user_id": test_user.id,
        "email": test_user.email,
        "conversation_id": "01JCONV00000000000000000001",
        "room_name": "pos-voice-01JCONV00000000000000000001",
        "token_use": "voice_delegation",
        "iss": "personal-os-api",
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    secret = test_settings.companion_jwt_secret
    token = jwt.encode(payload, secret, algorithm="HS256")

    # Mock session and user repo
    mock_session = AsyncMock()
    mock_user_repo = AsyncMock()
    mock_user_repo.get.return_value = test_user

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "personal_os.api.deps.SqlUserRepository",
            lambda s: mock_user_repo,
        )
        resolved_user = await get_current_user(
            credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
            session=mock_session,
            settings=test_settings,
        )
        assert resolved_user.id == test_user.id
        assert resolved_user.cognito_sub == test_user.cognito_sub


@pytest.mark.asyncio
async def test_voice_health_endpoint(app: FastAPI, test_settings: Settings):
    """Health check endpoint returns configuration readiness."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        res = await client.get("/api/v1/voice/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["livekit_configured"] is True
        assert data["environment"] == "test"
