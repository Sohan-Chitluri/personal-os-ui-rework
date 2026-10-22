"""Async PostgreSQL engine and session factory.

PostgreSQL is the canonical operational database. SQLite URLs are rejected
explicitly so async database operations cannot hang or silently exercise a
different schema/runtime contract.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from personal_os.config import Settings, get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def normalise_database_url(url: str) -> str:
    """Rewrite legacy/sync URLs to their async drivers.

    postgres:// / postgresql://        -> postgresql+asyncpg://
    postgresql+psycopg2://             -> postgresql+asyncpg://
    sslmode=...                        -> ssl=... (for asyncpg compatibility)
    """
    url = re.sub(r"^postgres(ql)?(\+psycopg2)?://", "postgresql+asyncpg://", url)
    url = re.sub(r"([?&])sslmode=", r"\1ssl=", url)
    return url


def build_engine(settings: Settings | None = None) -> AsyncEngine:
    """Create (or return the cached) async engine for the configured URL."""
    global _engine
    s = settings or get_settings()
    url = normalise_database_url(s.database_url)
    if not url.startswith("postgresql+asyncpg://"):
        raise ValueError(
            "DATABASE_URL must use PostgreSQL with asyncpg; SQLite is unsupported "
            "for application database operations. See docs/POSTGRES_DEVELOPMENT.md."
        )

    if _engine is not None:
        if str(_engine.url) == url:
            return _engine
        dispose_engine()

    _engine = create_async_engine(
        url,
        echo=s.db_echo,
        pool_size=s.db_pool_size,
        max_overflow=s.db_max_overflow,
        pool_pre_ping=True,
    )
    return _engine


def build_session_factory(engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    """Create (or return the cached) async session factory."""
    global _session_factory
    e = engine or build_engine()
    if _session_factory is not None:
        if _session_factory.kw.get("bind") is e:
            return _session_factory
        _session_factory = None

    _session_factory = async_sessionmaker(
        bind=e,
        expire_on_commit=False,
        autoflush=False,
    )
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a short-lived session with commit-on-success."""
    factory = build_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def dispose_engine() -> None:
    """Reset cached engine/factory (for tests and process shutdown)."""
    global _engine, _session_factory
    if _engine is not None:
        # Synchronous dispose is best-effort on shutdown; async callers use
        # adispose_engine().
        _engine.sync_engine.dispose()
    _engine = None
    _session_factory = None


async def adispose_engine() -> None:
    """Async variant of :func:`dispose_engine`."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


__all__ = [
    "build_engine",
    "build_session_factory",
    "get_session",
    "normalise_database_url",
    "dispose_engine",
    "adispose_engine",
]
