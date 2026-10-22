"""Tests for API authentication and authorization enforcement."""

from __future__ import annotations

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.db.models import User


def test_cognito_verification_accepts_valid_access_token_and_rejects_forgery(monkeypatch):
    from personal_os.api import deps
    from personal_os.config import Settings

    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = Settings(
        environment="staging",
        cognito_user_pool_id="us-test_123",
        cognito_app_client_id="client-123",
    )
    issuer = f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/{settings.cognito_user_pool_id}"
    monkeypatch.setitem(
        deps._jwks_clients,
        issuer,
        SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=signing_key.public_key())
        ),
    )
    claims = {
        "sub": "verified-sub",
        "iss": issuer,
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "token_use": "access",
        "client_id": "client-123",
    }
    legitimate = jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "test"})
    assert deps.verify_cognito_token(legitimate, settings)["sub"] == "verified-sub"

    forged = jwt.encode(
        claims | {"sub": "victim"},
        attacker_key,
        algorithm="RS256",
        headers={"kid": "test"},
    )
    with pytest.raises(jwt.PyJWTError):
        deps.verify_cognito_token(forged, settings)

    unsigned = jwt.encode(claims | {"sub": "victim"}, key=None, algorithm="none")
    with pytest.raises(jwt.PyJWTError):
        deps.verify_cognito_token(unsigned, settings)

    for invalid_claims in (
        claims | {"iss": issuer + "/wrong"},
        claims | {"client_id": "wrong-client"},
        claims | {"exp": int(time.time()) - 1},
        {key: value for key, value in claims.items() if key != "iat"},
    ):
        invalid = jwt.encode(
            invalid_claims, signing_key, algorithm="RS256", headers={"kid": "test"}
        )
        with pytest.raises((jwt.PyJWTError, ValueError)):
            deps.verify_cognito_token(invalid, settings)


async def test_missing_production_cognito_configuration_fails_startup():
    from personal_os.api.main import create_app
    from personal_os.config import Settings

    app = create_app(Settings(environment="production"))
    with pytest.raises(RuntimeError, match="Cognito pool and app client"):
        async with app.router.lifespan_context(app):
            pass


async def test_unauthenticated_request_returns_401(client: AsyncClient):
    """Endpoints requiring authentication return 401 when token is missing."""
    res = await client.get("/api/v1/projects")
    assert res.status_code == 401
    assert res.headers["content-type"] == "application/problem+json"
    body = res.json()
    assert body["status"] == 401
    assert "Missing authorization" in body["detail"]


async def test_invalid_bearer_token_returns_401(client: AsyncClient):
    """Malformed or invalid bearer tokens return 401."""
    res = await client.get("/api/v1/projects", headers={"Authorization": "Bearer ??"})
    assert res.status_code == 401
    body = res.json()
    assert body["status"] == 401


async def test_dev_static_token_authenticates(client: AsyncClient):
    """Static dev auth token authenticates as dev-user."""
    headers = {"Authorization": "Bearer test-secret-token"}
    res = await client.get("/api/v1/projects", headers=headers)
    assert res.status_code == 200
    assert "items" in res.json()


async def test_synthetic_test_token_authenticates(client: AsyncClient):
    """'test:<sub_id>' token authenticates as that sub user."""
    headers = {"Authorization": "Bearer test:alice"}
    res = await client.get("/api/v1/projects", headers=headers)
    assert res.status_code == 200
    assert "items" in res.json()


async def test_forged_jwt_cannot_impersonate_user(client: AsyncClient):
    """An unsigned payload cannot choose the authenticated user's subject."""
    forged = jwt.encode(
        {"sub": "victim", "exp": int(time.time()) + 300}, key=None, algorithm="none"
    )
    headers = {"Authorization": f"Bearer {forged}"}
    res = await client.get("/api/v1/projects", headers=headers)
    assert res.status_code == 401


async def test_valid_cognito_access_token_authenticates(client, api_app, monkeypatch):
    """A correctly signed access token with configured issuer/client is accepted."""
    from personal_os.api import deps
    from personal_os.config import Settings, get_settings

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = Settings(
        environment="staging",
        cognito_user_pool_id="us-test_123",
        cognito_app_client_id="client-123",
    )
    issuer = f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/{settings.cognito_user_pool_id}"
    monkeypatch.setitem(
        deps._jwks_clients,
        issuer,
        SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=private_key.public_key())
        ),
    )
    api_app.dependency_overrides[get_settings] = lambda: settings
    token = jwt.encode(
        {
            "sub": "verified-sub",
            "email": "verified@example.com",
            "iss": issuer,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "token_use": "access",
            "client_id": "client-123",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    response = await client.get("/api/v1/projects", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert "items" in response.json()


async def test_inactive_user_returns_403(client: AsyncClient, db_session: AsyncSession):
    """Inactive users are forbidden from accessing endpoints."""
    inactive_user = User(
        cognito_sub="inactive-sub",
        email="inactive@example.com",
        display_name="Deactivated",
        is_active=False,
    )
    db_session.add(inactive_user)
    await db_session.flush()

    headers = {"Authorization": "Bearer test:inactive-sub"}
    res = await client.get("/api/v1/projects", headers=headers)
    assert res.status_code == 403
    body = res.json()
    assert body["status"] == 403
    assert "inactive" in body["detail"].lower()


def test_staging_and_production_settings_reject_synthetic_auth():
    """Settings validation fails closed if dev auth options are configured in staging/prod."""
    from personal_os.config import Settings

    with pytest.raises(ValidationError, match="allow_synthetic_auth"):
        Settings(environment="staging", allow_synthetic_auth=True)

    with pytest.raises(ValidationError, match="allow_synthetic_auth"):
        Settings(environment="production", allow_synthetic_auth=True)

    with pytest.raises(ValidationError, match="dev_auth_token"):
        Settings(environment="staging", dev_auth_token="dev-secret-token")

    with pytest.raises(ValidationError, match="dev_auth_token"):
        Settings(environment="production", dev_auth_token="dev-secret-token")


def test_dotenv_dev_auth_does_not_leak_into_staging_settings():
    """Settings instantiated in staging/prod must not inherit dev auth from local .env."""
    from personal_os.config import Settings

    staging_settings = Settings(environment="staging")
    assert staging_settings.allow_synthetic_auth is False
    assert staging_settings.dev_auth_token is None

    prod_settings = Settings(environment="production")
    assert prod_settings.allow_synthetic_auth is False
    assert prod_settings.dev_auth_token is None


async def test_staging_environment_rejects_synthetic_tokens_at_auth_dependency(client, api_app):
    """In staging mode, synthetic identities and dev tokens are rejected with 401."""
    from personal_os.config import Settings, get_settings

    staging_settings = Settings(
        environment="staging",
        cognito_user_pool_id="us-test_123",
        cognito_app_client_id="client-123",
    )
    api_app.dependency_overrides[get_settings] = lambda: staging_settings

    # Synthetic test prefix token
    res = await client.get("/api/v1/projects", headers={"Authorization": "Bearer test:alice"})
    assert res.status_code == 401
    assert "Invalid or expired bearer token" in res.json()["detail"]

    # Synthetic user prefix token
    res = await client.get("/api/v1/projects", headers={"Authorization": "Bearer user:bob"})
    assert res.status_code == 401
    assert "Invalid or expired bearer token" in res.json()["detail"]

    # Static dev auth token
    res = await client.get(
        "/api/v1/projects", headers={"Authorization": "Bearer change-me-local-dev-token"}
    )
    assert res.status_code == 401
    assert "Invalid or expired bearer token" in res.json()["detail"]

    # Plain non-JWT token
    res = await client.get("/api/v1/projects", headers={"Authorization": "Bearer dev-user"})
    assert res.status_code == 401
    assert "Invalid or expired bearer token" in res.json()["detail"]
