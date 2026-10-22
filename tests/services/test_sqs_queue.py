"""SQS queue protocol tests using botocore Stubber (no AWS account required)."""

from __future__ import annotations

import asyncio
import json

import boto3
import pytest
from botocore.stub import Stubber

from personal_os.config import Settings
from personal_os.services.interfaces import QueueDelivery
from personal_os.services.queue import SqsCaptureQueue, build_capture_queue
from personal_os.services.worker import CaptureWorker

QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/123456789012/test-queue"


@pytest.fixture(autouse=True)
def inline_sdk_thread_calls(monkeypatch):
    """Keep deterministic Stubber calls on-loop; no threads or AWS I/O."""

    async def _inline(function, /, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _inline)


def _client():
    return boto3.client(
        "sqs",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def test_sqs_send_receive_ack_and_retry_use_receipt_handle():
    client = _client()
    queue = SqsCaptureQueue(QUEUE_URL, "us-east-1", client=client, wait_time_seconds=2)
    capture_id = "01CAPTURE000000000000000000"
    owner_id = "01OWNER00000000000000000000"
    receipt = "opaque-receipt-handle"
    with Stubber(client) as stubber:
        stubber.add_response(
            "send_message",
            {"MD5OfMessageBody": "a" * 32, "MessageId": "sqs-id"},
            {
                "QueueUrl": QUEUE_URL,
                "MessageBody": json.dumps(
                    {"capture_id": capture_id, "owner_id": owner_id}, separators=(",", ":")
                ),
                "MessageAttributes": {
                    "capture_id": {"DataType": "String", "StringValue": capture_id}
                },
            },
        )
        assert asyncio.run(queue.enqueue(capture_id, owner_id)) == "sqs-id"
        stubber.add_response(
            "receive_message",
            {
                "Messages": [
                    {
                        "MessageId": "sqs-id",
                        "ReceiptHandle": receipt,
                        "MD5OfBody": "a" * 32,
                        "Body": json.dumps({"capture_id": capture_id, "owner_id": owner_id}),
                        "Attributes": {"ApproximateReceiveCount": "3"},
                    }
                ]
            },
            {
                "QueueUrl": QUEUE_URL,
                "MaxNumberOfMessages": 10,
                "WaitTimeSeconds": 2,
                "VisibilityTimeout": 300,
                "AttributeNames": ["ApproximateReceiveCount"],
            },
        )
        delivery = asyncio.run(queue.dequeue())
        assert delivery == QueueDelivery(capture_id, owner_id, receipt, 3, "sqs-id")
        stubber.add_response(
            "change_message_visibility",
            {},
            {"QueueUrl": QUEUE_URL, "ReceiptHandle": receipt, "VisibilityTimeout": 8},
        )
        asyncio.run(queue.retry(delivery, delay_seconds=8))
        stubber.add_response(
            "change_message_visibility",
            {},
            {"QueueUrl": QUEUE_URL, "ReceiptHandle": receipt, "VisibilityTimeout": 300},
        )
        asyncio.run(queue.extend_visibility(delivery))
        stubber.add_response(
            "delete_message",
            {},
            {"QueueUrl": QUEUE_URL, "ReceiptHandle": receipt},
        )
        asyncio.run(queue.ack(delivery))


def test_sqs_receive_uses_bounded_long_poll_batch_and_visibility():
    client = _client()
    queue = SqsCaptureQueue(
        QUEUE_URL,
        "us-east-1",
        client=client,
        wait_time_seconds=20,
        visibility_timeout=900,
        max_messages=10,
    )
    with Stubber(client) as stubber:
        stubber.add_response(
            "receive_message",
            {},
            {
                "QueueUrl": QUEUE_URL,
                "MaxNumberOfMessages": 10,
                "WaitTimeSeconds": 20,
                "VisibilityTimeout": 900,
                "AttributeNames": ["ApproximateReceiveCount"],
            },
        )
        assert asyncio.run(queue.dequeue()) is None


def test_malformed_payload_becomes_unacked_redrive_delivery():
    client = _client()
    queue = SqsCaptureQueue(QUEUE_URL, "us-east-1", client=client, max_messages=1)
    with Stubber(client) as stubber:
        stubber.add_response(
            "receive_message",
            {
                "Messages": [
                    {
                        "MessageId": "poison-id",
                        "ReceiptHandle": "poison-receipt",
                        "MD5OfBody": "a" * 32,
                        "Body": "{invalid json",
                        "Attributes": {"ApproximateReceiveCount": "4"},
                    }
                ]
            },
            {
                "QueueUrl": QUEUE_URL,
                "MaxNumberOfMessages": 1,
                "WaitTimeSeconds": 20,
                "VisibilityTimeout": 300,
                "AttributeNames": ["ApproximateReceiveCount"],
            },
        )
        delivery = asyncio.run(queue.dequeue())
        assert delivery is not None
        assert delivery.invalid_reason == "invalid_payload"
        assert delivery.ack_token == "poison-receipt"
        assert delivery.receive_count == 4
        stubber.add_response(
            "change_message_visibility",
            {},
            {
                "QueueUrl": QUEUE_URL,
                "ReceiptHandle": "poison-receipt",
                "VisibilityTimeout": 300,
            },
        )
        asyncio.run(queue.retry(delivery, delay_seconds=300))


def test_worker_defers_invalid_delivery_without_acknowledging():
    class QueueStub:
        visibility_timeout = 900

        def __init__(self):
            self.retried = None
            self.acked = False

        async def dequeue(self):
            return QueueDelivery("", "", "receipt", 2, "poison", "invalid_payload")

        async def retry(self, delivery, delay_seconds=0):
            self.retried = (delivery, delay_seconds)

        async def ack(self, delivery):
            self.acked = True

    queue = QueueStub()
    worker = CaptureWorker(None, queue, None)
    assert asyncio.run(worker.run_once()) is False
    assert queue.retried == (
        QueueDelivery("", "", "receipt", 2, "poison", "invalid_payload"),
        900,
    )
    assert queue.acked is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"wait_time_seconds": 21},
        {"visibility_timeout": 0},
        {"max_messages": 11},
    ],
)
def test_sqs_configuration_bounds_are_enforced(kwargs):
    with pytest.raises(ValueError):
        SqsCaptureQueue(QUEUE_URL, "us-east-1", client=object(), **kwargs)


@pytest.mark.parametrize("url", ["http://localhost:4566/queue", "https://sqs.amazonaws.com/"])
def test_sqs_queue_url_must_be_https_and_name_a_queue(url):
    with pytest.raises(ValueError, match="queue URL"):
        SqsCaptureQueue(url, "us-east-1", client=object())


def test_sqs_backend_requires_queue_url_and_builds_with_injected_client():
    settings = Settings(environment="production", queue_backend="sqs")
    with pytest.raises(RuntimeError, match="SQS_QUEUE_URL"):
        build_capture_queue(settings)
    queue = build_capture_queue(
        settings.model_copy(update={"sqs_queue_url": QUEUE_URL}), sqs_client=_client()
    )
    assert isinstance(queue, SqsCaptureQueue)
