"""At-least-once capture queue implementations."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlparse

from personal_os.services.interfaces import CaptureQueue, QueueDelivery

log = logging.getLogger(__name__)


@dataclass
class _LocalMessage:
    capture_id: str
    owner_id: str
    receive_count: int = 0


class LocalCaptureQueue:
    """Volatile, single-process queue for development and deterministic tests."""

    durable = False

    def __init__(
        self,
        *,
        fail_enqueue: Callable[[str, str], None] | None = None,
        fail_dequeue: Callable[[], None] | None = None,
    ) -> None:
        self._ready: deque[_LocalMessage] = deque()
        self._in_flight: dict[str, _LocalMessage] = {}
        self.fail_enqueue = fail_enqueue
        self.fail_dequeue = fail_dequeue

    async def enqueue(self, capture_id: str, owner_id: str) -> str:
        if self.fail_enqueue is not None:
            self.fail_enqueue(capture_id, owner_id)
        self._ready.append(_LocalMessage(capture_id, owner_id))
        return capture_id

    async def dequeue(self) -> QueueDelivery | None:
        if self.fail_dequeue is not None:
            self.fail_dequeue()
        if not self._ready:
            return None
        msg = self._ready.popleft()
        msg.receive_count += 1
        token = str(uuid.uuid4())
        self._in_flight[token] = msg
        return QueueDelivery(msg.capture_id, msg.owner_id, token, msg.receive_count)

    async def ack(self, delivery: QueueDelivery) -> None:
        self._in_flight.pop(delivery.ack_token, None)

    async def retry(self, delivery: QueueDelivery, delay_seconds: int = 0) -> None:
        msg = self._in_flight.pop(delivery.ack_token, None)
        if msg is None:
            return
        # Local tests model visibility as immediate redelivery; production
        # timing semantics are exercised by the SQS adapter's stubbed client.
        self._ready.appendleft(msg)

    async def extend_visibility(self, delivery: QueueDelivery) -> None:
        # In-memory delivery is owned by this process and has no lease expiry.
        return None

    # Compatibility for focused tests from the previous local queue contract.
    def requeue_message(self, message: tuple[str, str]) -> None:
        for token, current in tuple(self._in_flight.items()):
            if (current.capture_id, current.owner_id) == message:
                del self._in_flight[token]
                self._ready.appendleft(current)
                return
        self._ready.appendleft(_LocalMessage(*message))

    @property
    def size(self) -> int:
        return len(self._ready)

    @property
    def in_flight(self) -> int:
        return len(self._in_flight)

    def redelivery_count(self, message_id: str) -> int:
        return max((m.receive_count for m in self._ready if m.capture_id == message_id), default=0)

    async def close(self) -> None:
        # Work in flight is not acknowledged; a local process shutdown cannot
        # preserve volatile messages, which is why this backend is local-only.
        self._ready.clear()
        self._in_flight.clear()


class SqsCaptureQueue:
    """SQS adapter using long polling and visibility timeout leases.

    ``client`` is injectable for botocore Stubber/unit tests. Messages contain
    only capture and owner IDs; receipt handles are kept in QueueDelivery and
    are never logged. SQS redrive policy provides the DLQ boundary.
    """

    durable = True

    def __init__(
        self,
        queue_url: str,
        region: str,
        *,
        client: Any | None = None,
        wait_time_seconds: int = 20,
        visibility_timeout: int = 300,
        max_messages: int = 10,
    ) -> None:
        parsed_url = urlparse(queue_url)
        if (
            parsed_url.scheme != "https"
            or not parsed_url.hostname
            or not parsed_url.path.strip("/")
        ):
            raise ValueError("SQS queue URL must use HTTPS")
        if not 0 <= wait_time_seconds <= 20:
            raise ValueError("SQS long-poll wait must be between 0 and 20 seconds")
        if not 1 <= visibility_timeout <= 43200:
            raise ValueError("SQS visibility timeout must be between 1 and 43200 seconds")
        if not 1 <= max_messages <= 10:
            raise ValueError("SQS receive batch must be between 1 and 10 messages")
        if client is None:
            import boto3  # type: ignore[import-untyped]

            client = boto3.client("sqs", region_name=region)
        self.queue_url = queue_url
        self.client = client
        self.wait_time_seconds = wait_time_seconds
        self.visibility_timeout = visibility_timeout
        self.max_messages = max_messages
        self._buffer: deque[QueueDelivery] = deque()
        self._closed = False

    async def enqueue(self, capture_id: str, owner_id: str) -> str:
        response = await asyncio.to_thread(
            self.client.send_message,
            QueueUrl=self.queue_url,
            MessageBody=json.dumps(
                {"capture_id": capture_id, "owner_id": owner_id}, separators=(",", ":")
            ),
            MessageAttributes={"capture_id": {"DataType": "String", "StringValue": capture_id}},
        )
        message_id = response.get("MessageId")
        if not message_id:
            raise RuntimeError("SQS send_message returned no MessageId")
        log.info("Capture queued capture_id=%s message_id=%s", capture_id, message_id)
        return cast(str, message_id)

    async def dequeue(self) -> QueueDelivery | None:
        if self._closed:
            return None
        if not self._buffer:
            response = await asyncio.to_thread(
                self.client.receive_message,
                QueueUrl=self.queue_url,
                MaxNumberOfMessages=self.max_messages,
                WaitTimeSeconds=self.wait_time_seconds,
                VisibilityTimeout=self.visibility_timeout,
                AttributeNames=["ApproximateReceiveCount"],
            )
            for raw in response.get("Messages", []):
                receipt = raw.get("ReceiptHandle")
                if not receipt:
                    log.warning(
                        "SQS returned a message without a receipt handle; leaving it unacked"
                    )
                    continue
                message_id = raw.get("MessageId")
                try:
                    receive_count = int(
                        raw.get("Attributes", {}).get("ApproximateReceiveCount", "1")
                    )
                except (TypeError, ValueError):
                    receive_count = 1
                try:
                    body = json.loads(raw["Body"])
                    capture_id = body["capture_id"]
                    owner_id = body["owner_id"]
                    if not isinstance(capture_id, str) or not isinstance(owner_id, str):
                        raise ValueError("capture_id and owner_id must be strings")
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    # Keep a delivery shell so the worker can defer visibility;
                    # SQS redrive then moves poison payloads to the DLQ.
                    log.warning(
                        "Malformed SQS capture message message_id=%s error=invalid_payload",
                        message_id,
                    )
                    self._buffer.append(
                        QueueDelivery("", "", receipt, receive_count, message_id, "invalid_payload")
                    )
                    continue
                self._buffer.append(
                    QueueDelivery(capture_id, owner_id, receipt, receive_count, message_id)
                )
        if self._buffer:
            return self._buffer.popleft()
        return None

    async def ack(self, delivery: QueueDelivery) -> None:
        await asyncio.to_thread(
            self.client.delete_message,
            QueueUrl=self.queue_url,
            ReceiptHandle=delivery.ack_token,
        )
        log.info(
            "Capture acknowledged capture_id=%s receive_count=%d",
            delivery.capture_id,
            delivery.receive_count,
        )

    async def retry(self, delivery: QueueDelivery, delay_seconds: int = 0) -> None:
        # The message remains durable and unacknowledged. Changing visibility
        # controls retry timing; a crash naturally returns it after lease expiry.
        bounded_delay = max(0, min(int(delay_seconds), 900))
        await asyncio.to_thread(
            self.client.change_message_visibility,
            QueueUrl=self.queue_url,
            ReceiptHandle=delivery.ack_token,
            VisibilityTimeout=bounded_delay,
        )
        log.warning(
            "Capture deferred capture_id=%s receive_count=%d delay_seconds=%d",
            delivery.capture_id,
            delivery.receive_count,
            bounded_delay,
        )

    async def extend_visibility(self, delivery: QueueDelivery) -> None:
        # SQS may return several messages in a receive batch. While the worker
        # handles the first, keep all buffered leases alive too.
        leases = [delivery, *self._buffer]
        stale_buffered: list[QueueDelivery] = []
        for lease in leases:
            try:
                await asyncio.to_thread(
                    self.client.change_message_visibility,
                    QueueUrl=self.queue_url,
                    ReceiptHandle=lease.ack_token,
                    VisibilityTimeout=self.visibility_timeout,
                )
            except Exception as exc:
                error_code = ""
                response = getattr(exc, "response", None)
                if isinstance(response, dict):
                    error_code = str(response.get("Error", {}).get("Code", ""))
                exc_str = str(exc)

                is_stale_handle = (
                    error_code
                    in ("ReceiptHandleIsInvalid", "MessageNotInflight", "InvalidParameterValue")
                    or "ReceiptHandleIsInvalid" in exc_str
                    or "MessageNotInflight" in exc_str
                )

                if is_stale_handle:
                    log.warning(
                        "SQS message lease expired or handle is stale for capture_id=%s (error=%s)",
                        lease.capture_id,
                        error_code or exc_str,
                    )
                    if lease in self._buffer:
                        stale_buffered.append(lease)
                else:
                    raise

        for stale in stale_buffered:
            try:
                self._buffer.remove(stale)
            except ValueError:
                pass

    async def close(self) -> None:
        self._closed = True
        # Buffered SQS receipts remain unacknowledged; visibility expiry returns
        # them for redelivery. Drop local references and close HTTP pool.
        self._buffer.clear()
        close = getattr(self.client, "close", None)
        if close is not None:
            await asyncio.to_thread(close)


def build_capture_queue(settings: Any, *, sqs_client: Any | None = None) -> CaptureQueue:
    """Construct the configured backend; refuse unsafe or incomplete modes."""
    if settings.queue_backend == "inline" and settings.environment in ("local", "test"):
        return LocalCaptureQueue()
    if settings.queue_backend == "sqs":
        if not settings.sqs_queue_url:
            raise RuntimeError("SQS_QUEUE_URL is required when QUEUE_BACKEND=sqs")
        return SqsCaptureQueue(
            settings.sqs_queue_url,
            settings.sqs_region,
            client=sqs_client,
            wait_time_seconds=settings.sqs_wait_time_seconds,
            visibility_timeout=settings.sqs_visibility_timeout,
            max_messages=settings.sqs_max_messages,
        )
    raise RuntimeError("The in-memory capture queue is supported only in local/test")


__all__ = ["LocalCaptureQueue", "SqsCaptureQueue", "build_capture_queue"]
