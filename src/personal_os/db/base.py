"""Declarative base, portable column types, and shared mixins.

PostgreSQL (via asyncpg) is the canonical operational database. Generic SQL
types keep offline dialect compilation straightforward; SQLite is used only
for pure type-semantics checks, not application DB sessions.

Design decisions (see docs/DATA_MODEL.md and docs/DECISIONS.md):
  * Primary keys are ULIDs (26-char Crockford base32), stored as CHAR(26).
    The architecture documents describe IDs as "ULID" (ARCHITECTURE_DECISIONS
    section 5) — we store them as strings rather than native uuid so the same
    DDL runs unchanged on SQLite. The API layer presents them as opaque strings.
  * Timestamps are timezone-aware and normalize to UTC. PostgreSQL stores them
    as timestamptz; SQLite dialect checks exercise the Python conversion only.
  * Every mutable operational record carries `revision` for optimistic locking.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import ulid as _ulid  # type: ignore[import-untyped]
from sqlalchemy import CHAR, DateTime, Integer, String, TypeDecorator, func
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 26 chars: ULID = 48-bit timestamp + 80-bit randomness, Crockford base32.
ULID_LENGTH = 26


def new_ulid() -> str:
    """Return a fresh, lexicographically-sortable ULID string."""
    return str(_ulid.ulid())


def utcnow() -> datetime:
    """Timezone-aware UTC now (Python-side default)."""
    return datetime.now(UTC)


class ULIDType(TypeDecorator):
    """Portable ULID column type.

    Stored as CHAR(26) on every dialect. Binds/returns plain ``str`` so callers
    never deal with dialect-specific uuid objects.
    """

    impl = CHAR(ULID_LENGTH)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return str(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return str(value)


class JSONType(TypeDecorator):
    """Portable JSON column type.

    Uses native ``JSON`` on PostgreSQL/SQLite (both support it) via the generic
    :class:`sqlalchemy.JSON`; kept as a named alias so models read consistently
    and future dialect tuning has one place to change.
    """

    impl = String
    cache_ok = True


class TZDateTime(TypeDecorator):
    """Portable timezone-aware DateTime.

    PostgreSQL natively returns tz-aware datetimes with asyncpg. SQLite's
    built-in driver drops tzinfo on result retrieval. This decorator guarantees
    consistent timezone-aware UTC datetime values across both dialects.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        dt = cast(datetime, value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        dt = cast(datetime, value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class ULIDPrimaryKeyMixin:
    """Adds a ULID ``id`` primary key."""

    id: Mapped[str] = mapped_column(ULIDType, primary_key=True, default=new_ulid, nullable=False)


class TimestampMixin:
    """Adds server-managed ``created_at`` / ``updated_at`` columns."""

    created_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        default=utcnow,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
        nullable=False,
    )


class RevisionMixin:
    """Adds the optimistic-locking ``revision`` counter."""

    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)


class SoftDeleteMixin:
    """Adds a nullable ``deleted_at`` for soft deletes."""

    deleted_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True, default=None)


__all__ = [
    "Base",
    "ULIDType",
    "JSONType",
    "TZDateTime",
    "ULIDPrimaryKeyMixin",
    "TimestampMixin",
    "RevisionMixin",
    "SoftDeleteMixin",
    "new_ulid",
    "utcnow",
    "ULID_LENGTH",
]
