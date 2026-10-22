"""Fixtures for protocol and device API tests (M2-B).

Uses an isolated in-memory database to allow automated testing without
external services.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.websockets import WebSocketDisconnect

from personal_os.api.deps import get_db_session
from personal_os.api.main import create_app
from personal_os.api.routes.companion_ws import companion_websocket_endpoint
from personal_os.config import Settings, get_settings
from personal_os.db.base import Base, new_ulid
from personal_os.db.models import User


@pytest_asyncio.fixture
async def proto_engine():
    """In-memory SQLite async engine with all tables created."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def proto_session(proto_engine) -> AsyncIterator[AsyncSession]:
    """Provide a scoped session over the in-memory engine."""
    factory = async_sessionmaker(bind=proto_engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def test_user(proto_session: AsyncSession) -> User:
    """Create primary test user."""
    user = User(
        id=new_ulid(),
        cognito_sub="user-alice",
        email="alice@example.com",
        display_name="Alice",
    )
    proto_session.add(user)
    await proto_session.commit()
    return user


@pytest_asyncio.fixture
async def other_user(proto_session: AsyncSession) -> User:
    """Create second distinct user for isolation/cross-user scoping tests."""
    user = User(
        id=new_ulid(),
        cognito_sub="user-bob",
        email="bob@example.com",
        display_name="Bob",
    )
    proto_session.add(user)
    await proto_session.commit()
    return user


@pytest_asyncio.fixture
async def api_app(proto_session: AsyncSession, proto_engine):
    """FastAPI app with overridden DB session factory."""
    settings = Settings(
        environment="test",
        dev_auth_token="test-secret-token-32-chars-long-123456",
        companion_jwt_secret="test-secret-token-32-chars-long-123456",
        allow_synthetic_auth=True,
    )
    app = create_app(settings)

    factory = async_sessionmaker(bind=proto_engine, expire_on_commit=False, autoflush=False)

    async def _override_db_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db_session] = _override_db_session
    app.dependency_overrides[get_settings] = lambda: settings

    async with app.router.lifespan_context(app):
        from personal_os.api.companion_broker import CompanionBroker
        app.state.db_session_factory = factory
        app.state.companion_broker = CompanionBroker()
        yield app
        await app.state.companion_broker.stop_coordinator()
        app.state.companion_broker.clear()

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_client(api_app, test_user: User) -> AsyncIterator[AsyncClient]:
    """Authenticated HTTP client for test_user."""
    headers = {"Authorization": f"Bearer test:{test_user.cognito_sub}"}
    async with AsyncClient(
        transport=ASGITransport(app=api_app),
        base_url="http://testserver",
        headers=headers,
    ) as c:
        yield c


@pytest_asyncio.fixture
async def other_client(api_app, other_user: User) -> AsyncIterator[AsyncClient]:
    """Authenticated HTTP client for other_user."""
    headers = {"Authorization": f"Bearer test:{other_user.cognito_sub}"}
    async with AsyncClient(
        transport=ASGITransport(app=api_app),
        base_url="http://testserver",
        headers=headers,
    ) as c:
        yield c


@pytest_asyncio.fixture
async def unauth_client(api_app) -> AsyncIterator[AsyncClient]:
    """Unauthenticated HTTP client."""
    async with AsyncClient(
        transport=ASGITransport(app=api_app),
        base_url="http://testserver",
    ) as c:
        yield c


class InMemoryWebSocket:
    """In-memory async WebSocket channel simulating bidirectional frame transport."""

    def __init__(self, app) -> None:
        self.app = app
        self.accepted = False
        self.closed = False
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self._client_to_server: asyncio.Queue[str | None] = asyncio.Queue()
        self._server_to_client: asyncio.Queue[str] = asyncio.Queue()

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        msg = await self._client_to_server.get()
        if msg is None:
            raise WebSocketDisconnect(code=self.close_code or 1000)
        return msg

    async def send_text(self, text: str) -> None:
        await self._server_to_client.put(text)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_code = code
        self.close_reason = reason
        await self._client_to_server.put(None)

    # Client-side helper methods
    async def send_json(self, data: dict) -> None:
        await self._client_to_server.put(json.dumps(data))

    async def receive_json(self) -> dict:
        text = await asyncio.wait_for(self._server_to_client.get(), timeout=2.0)
        return json.loads(text)

    async def disconnect(self) -> None:
        await self._client_to_server.put(None)


@asynccontextmanager
async def open_companion_ws(api_app) -> AsyncIterator[InMemoryWebSocket]:
    ws = InMemoryWebSocket(api_app)
    task = asyncio.create_task(companion_websocket_endpoint(ws))  # type: ignore[arg-type]
    try:
        yield ws
    finally:
        await ws.disconnect()
        await task

