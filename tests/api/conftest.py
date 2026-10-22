"""Pytest fixtures for API layer tests (M1-C)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.api.deps import get_db_session
from personal_os.api.main import create_app
from personal_os.config import Settings
from personal_os.db.models import User


@pytest_asyncio.fixture
async def other_user(db_session: AsyncSession) -> User:
    """Create a second distinct user for isolation/owner scoping tests."""
    user = User(
        cognito_sub="other-user-sub",
        email="other@example.com",
        display_name="Other User",
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def api_app(db_session: AsyncSession):
    """FastAPI application configured with in-memory DB session override."""
    settings = Settings(
        environment="test",
        dev_auth_token="test-secret-token",
        allow_synthetic_auth=True,
    )
    app = create_app(settings)

    async def _override_db_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    from personal_os.config import get_settings

    app.dependency_overrides[get_db_session] = _override_db_session
    app.dependency_overrides[get_settings] = lambda: settings
    async with app.router.lifespan_context(app):
        yield app
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(api_app) -> AsyncIterator[AsyncClient]:
    """Unauthenticated HTTP client."""
    async with AsyncClient(
        transport=ASGITransport(app=api_app),
        base_url="http://testserver",
    ) as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(test_user: User) -> dict[str, str]:
    """Bearer authorization header for test_user."""
    token = test_user.cognito_sub or f"test:{test_user.id}"
    return {"Authorization": f"Bearer test:{token}"}


@pytest_asyncio.fixture
async def other_auth_headers(other_user: User) -> dict[str, str]:
    """Bearer authorization header for other_user."""
    return {"Authorization": f"Bearer test:{other_user.cognito_sub}"}


@pytest_asyncio.fixture
async def auth_client(api_app, auth_headers: dict[str, str]) -> AsyncIterator[AsyncClient]:
    """Authenticated HTTP client for test_user."""
    async with AsyncClient(
        transport=ASGITransport(app=api_app),
        base_url="http://testserver",
        headers=auth_headers,
    ) as c:
        yield c


@pytest_asyncio.fixture
async def other_client(api_app, other_auth_headers: dict[str, str]) -> AsyncIterator[AsyncClient]:
    """Authenticated HTTP client for other_user."""
    async with AsyncClient(
        transport=ASGITransport(app=api_app),
        base_url="http://testserver",
        headers=other_auth_headers,
    ) as c:
        yield c
