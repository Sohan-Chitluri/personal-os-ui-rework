"""PostgreSQL transaction lock that keeps each owner's feed commit ordered."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def lock_owner_feed(session: AsyncSession, owner_id: str) -> None:
    """Serialize sequence allocation until the transaction commits or rolls back."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": f"sync-feed-owner:{owner_id}"},
    )
