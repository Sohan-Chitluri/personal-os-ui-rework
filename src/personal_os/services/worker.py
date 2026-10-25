"""CaptureWorker — dequeues capture messages and processes them.

Loop semantics:
    1. dequeue() → message (capture_id, owner_id). None → idle (asyncio sleep).
    2. process_capture() via a *fresh* ProcessingServiceImpl per message
       (each message = one session/transaction = one unit of work).
    3. Success → ack (message leaves the system). Terminal failure (retries
       exhausted → 'dead') → ack as well: the processing row records the
       terminal state; redelivery would short-circuit as idempotent no-op.
    4. Retryable failure → requeue (redelivery). The processing row's attempt
       counter governs retry limits, so redelivery beyond max_attempts
       short-circuits to 'dead' — retries never duplicate tasks.

Worker crash semantics: if the process dies before ack, the message stays in
the queue's in-flight set (local queue) or reappears after visibility timeout
(SQS). Either way the recovery sweep re-enqueues unfinished processing rows at
startup, so crash recovery does not depend on the queue's redelivery behaviour.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import async_sessionmaker

from personal_os.core.extraction import SemanticExtractor
from personal_os.services.interfaces import CaptureQueue, QueueDelivery, ServiceNotFound
from personal_os.services.processing import ProcessingServiceImpl

log = logging.getLogger(__name__)


class OutboxDispatcher(Protocol):
    async def run_once(self) -> int: ...


class CaptureWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        queue: CaptureQueue,
        extractor: SemanticExtractor,
        *,
        task_sink: Any = None,
        outbox_dispatcher: OutboxDispatcher | None = None,
        idle_sleep: float = 0.05,
        retry_backoff: float = 0.1,
        retry_backoff_max: float = 2.0,
        outbox_poll_interval: float = 1.0,
    ) -> None:
        self.session_factory = session_factory
        self.queue = queue
        self.extractor = extractor
        self.task_sink = task_sink
        self.outbox_dispatcher = outbox_dispatcher
        self.outbox_poll_interval = outbox_poll_interval
        self.idle_sleep = idle_sleep
        # Bounded exponential backoff between redeliveries of the same
        # capture — prevents tight retry loops hammering a failing pipeline.
        self.retry_backoff = retry_backoff
        self.retry_backoff_max = retry_backoff_max
        self._backoff: dict[str, float] = {}  # capture_id → current delay
        self.stopping = False
        self.processed = 0
        self.retried = 0
        self.failed = 0

    async def run_once(self) -> bool:
        """Process one message. Returns True if a message was handled."""
        if self.outbox_dispatcher is not None:
            try:
                # Publish committed outbox rows before polling. The dispatcher
                # must mark rows sent only after enqueue succeeds.
                await self.outbox_dispatcher.run_once()
            except Exception:  # noqa: BLE001 — queue polling still serves older work
                log.exception("Outbox dispatch attempt failed")
        message = await self.queue.dequeue()
        if message is None:
            await asyncio.sleep(self.idle_sleep)
            return False
        if message.invalid_reason is not None:
            log.warning(
                "Deferring invalid queue delivery message_id=%s receive_count=%d reason=%s",
                message.message_id,
                message.receive_count,
                message.invalid_reason,
            )
            # Keep it unacknowledged so SQS redrive can move it to the DLQ.
            # Full visibility delay avoids a poison-message hot loop.
            await self._retry(
                message,
                getattr(self.queue, "visibility_timeout", 300),
            )
            self.retried += 1
            return False
        capture_id = message.capture_id
        try:
            return await self.handle_message(message)
        except Exception:  # noqa: BLE001 — worker must survive
            # Covers session-factory errors (DB outage) raised *outside*
            # handle_message's own guard. Durable work is not lost: the
            # processing row still exists; requeue with backoff.
            log.exception("Worker error handling %s", capture_id)
            await self._retry(message, self.retry_backoff)
            self.retried += 1
            return False

    async def handle_message(self, delivery: QueueDelivery) -> bool:
        """Process one message; ack on success/terminal, requeue on retryable."""
        capture_id = delivery.capture_id
        owner_id = delivery.owner_id
        visibility_task = asyncio.create_task(self._maintain_visibility(delivery))
        try:
            async with self.session_factory() as session:
                service = ProcessingServiceImpl(session, self.extractor, task_sink=self.task_sink)
                try:
                    result = await service.process_capture(capture_id, owner_id)
                    await session.commit()
                except ServiceNotFound:
                    # An outbox only publishes committed captures. Missing
                    # captures are therefore permanent/deleted and can ack.
                    log.warning("Capture %s not found — acking (skipping)", capture_id)
                    self._backoff.pop(capture_id, None)
                    await self.queue.ack(delivery)
                    return True
                except Exception:  # noqa: BLE001 — worker must survive
                    log.exception("Worker error processing %s", capture_id)
                    await self._retry(delivery, self.retry_backoff)
                    self.retried += 1
                    return False
        finally:
            visibility_task.cancel()
            await asyncio.gather(visibility_task, return_exceptions=True)

        self._backoff.pop(capture_id, None)  # succeeded or terminal — reset

        if result.status == "succeeded":
            await self.queue.ack(delivery)
            self.processed += 1
            return True
        # 'failed' (retryable) or 'dead' (terminal).
        if result.status == "dead":
            await self.queue.ack(delivery)  # terminal state is durable in DB
            self.failed += 1
        else:
            # Bounded backoff: double per retry, capped at retry_backoff_max.
            delay = min(
                self._backoff.get(capture_id, self.retry_backoff) * 2,
                self.retry_backoff_max,
            )
            self._backoff[capture_id] = delay
            await self._retry(delivery, delay)
            self.retried += 1
        return True

    async def _retry(self, delivery: QueueDelivery, delay_seconds: float) -> None:
        """Keep the current delivery unacknowledged and defer its visibility."""
        try:
            effective_delay = max(1, math.ceil(delay_seconds)) if delay_seconds > 0 else 0
            await self.queue.retry(delivery, delay_seconds=effective_delay)
        except Exception:  # noqa: BLE001 — message remains unacked for lease expiry
            log.exception("Could not defer queue delivery capture_id=%s", delivery.capture_id)

    async def _maintain_visibility(self, delivery: QueueDelivery) -> None:
        """Extend SQS lease during slow extraction; cancellation leaves it unacked."""
        extend = getattr(self.queue, "extend_visibility", None)
        if extend is None:
            return
        interval = max(1.0, getattr(self.queue, "visibility_timeout", 300) / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                await extend(delivery)
            except Exception:  # noqa: BLE001 — lease expiry safely permits redelivery
                log.exception(
                    "Could not extend queue visibility capture_id=%s", delivery.capture_id
                )

    async def run(self, *, max_messages: int | None = None) -> None:
        """Main loop. max_messages=None → run until stopped."""
        handled = 0
        outbox_task = None
        if self.outbox_dispatcher is not None:
            outbox_task = asyncio.create_task(self._run_outbox_pump())
        try:
            while not self.stopping:
                if max_messages is not None and handled >= max_messages:
                    break
                did_work = await self.run_once()
                if did_work:
                    handled += 1
        finally:
            if outbox_task is not None:
                outbox_task.cancel()
                await asyncio.gather(outbox_task, return_exceptions=True)

    async def _run_outbox_pump(self) -> None:
        """Poll independently of SQS long-poll waits for prompt dispatch."""
        dispatcher = self.outbox_dispatcher
        if dispatcher is None:
            return
        while not self.stopping:
            try:
                await dispatcher.run_once()
            except Exception:  # noqa: BLE001 — durable outbox rows remain retryable
                log.exception("Outbox dispatch attempt failed")
            await asyncio.sleep(self.outbox_poll_interval)

    async def recover_pending(self, *, limit: int = 100) -> int:
        """Repair missing dispatch intents and publish the ready outbox batch.

        Volatile local queues re-arm dispatched intents for unfinished captures;
        durable SQS leaves dispatched intents alone and relies on SQS redelivery.
        """
        async with self.session_factory() as session:
            from personal_os.services.tasks import CaptureServiceImpl

            capture_service = CaptureServiceImpl(session, queue=self.queue)
            n = await capture_service.recover_pending_captures(limit=limit)
            await session.commit()
        if self.outbox_dispatcher is not None:
            n += await self.outbox_dispatcher.run_once()
        if n:
            log.info("Recovery sweep re-enqueued %d pending captures", n)
        return n

    def stop(self) -> None:
        self.stopping = True


__all__ = ["CaptureWorker"]
