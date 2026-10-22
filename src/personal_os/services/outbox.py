"""Publish committed capture dispatch intents to the configured queue.

The database transaction that creates a capture also creates its outbox row.
Sending and marking the row dispatched cannot be atomic, so a crash after
SendMessage but before commit may duplicate a delivery. Capture processing is
idempotent and serializes duplicate deliveries by processing-row lock.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from personal_os.db.models import Capture, CaptureDispatchOutbox
from personal_os.services.interfaces import CaptureQueue

log = logging.getLogger(__name__)


class CaptureOutboxDispatcher:
    """Poll and publish pending capture dispatch rows with bounded backoff."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        queue: CaptureQueue,
        *,
        batch_size: int = 20,
        retry_base_seconds: int = 1,
        retry_max_seconds: int = 300,
    ) -> None:
        if not 1 <= batch_size <= 100:
            raise ValueError("batch_size must be between 1 and 100")
        self.session_factory = session_factory
        self.queue = queue
        self.batch_size = batch_size
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds

    async def run_once(self) -> int:
        """Publish one locked batch, returning the number successfully sent."""
        now = datetime.now(UTC)
        sent = 0
        async with self.session_factory() as session:
            stmt = (
                select(CaptureDispatchOutbox, Capture.owner_id)
                .join(Capture, Capture.id == CaptureDispatchOutbox.capture_id)
                .where(
                    CaptureDispatchOutbox.state == "pending",
                    or_(
                        CaptureDispatchOutbox.next_attempt_at.is_(None),
                        CaptureDispatchOutbox.next_attempt_at <= now,
                    ),
                )
                .order_by(CaptureDispatchOutbox.created_at)
                .limit(self.batch_size)
                .with_for_update(skip_locked=True)
            )
            rows = (await session.execute(stmt)).all()
            for outbox, owner_id in rows:
                outbox.attempts += 1
                try:
                    await self.queue.enqueue(outbox.capture_id, owner_id)
                except Exception as exc:  # noqa: BLE001 — preserve durable outbox intent
                    delay = min(
                        self.retry_base_seconds * (2 ** min(outbox.attempts - 1, 20)),
                        self.retry_max_seconds,
                    )
                    outbox.next_attempt_at = now + timedelta(seconds=delay)
                    outbox.last_error = type(exc).__name__
                    log.warning(
                        "Capture dispatch failed capture_id=%s error_type=%s retry_seconds=%s",
                        outbox.capture_id,
                        type(exc).__name__,
                        delay,
                    )
                else:
                    outbox.state = "dispatched"
                    outbox.dispatched_at = now
                    outbox.next_attempt_at = None
                    outbox.last_error = None
                    sent += 1
            await session.commit()
        return sent


__all__ = ["CaptureOutboxDispatcher"]
