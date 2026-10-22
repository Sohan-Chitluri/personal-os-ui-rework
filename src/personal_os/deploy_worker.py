"""Runtime entry point for the Personal OS capture worker."""

from __future__ import annotations

import asyncio
import logging
import signal

from personal_os.config import get_settings
from personal_os.core.extraction import SemanticExtractor
from personal_os.core.llm import get_provider
from personal_os.db.session import adispose_engine, build_engine, build_session_factory
from personal_os.services.outbox import CaptureOutboxDispatcher
from personal_os.services.queue import build_capture_queue
from personal_os.services.worker import CaptureWorker

log = logging.getLogger("personal_os.worker")


async def _heartbeat(environment: str, region_name: str | None = None) -> None:
    client = None
    while True:
        try:
            if client is None:
                import boto3  # type: ignore[import-untyped]

                client = boto3.client("cloudwatch", region_name=region_name)
            client.put_metric_data(
                Namespace=f"PersonalOS/{environment}",
                MetricData=[{"MetricName": "WorkerHeartbeat", "Value": 1, "Unit": "Count"}],
            )
            log.info("Worker heartbeat published")
        except Exception as exc:  # noqa: BLE001 — preserve worker during telemetry outage
            log.error("Worker heartbeat failed: %s", type(exc).__name__)
        await asyncio.sleep(60)


async def run_worker() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    queue = build_capture_queue(settings)
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    provider = get_provider(settings)
    outbox_dispatcher = CaptureOutboxDispatcher(session_factory, queue)
    worker = CaptureWorker(
        session_factory,
        queue,
        SemanticExtractor(provider),
        outbox_dispatcher=outbox_dispatcher,
    )
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.stop)

    heartbeat = asyncio.create_task(_heartbeat(settings.environment, settings.aws_region))
    log.info(
        "Capture worker starting; backend=%s environment=%s",
        settings.queue_backend,
        settings.environment,
    )
    try:
        await worker.recover_pending()
        await worker.run()
    finally:
        worker.stop()
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        await provider.close()
        close = getattr(queue, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result
        await adispose_engine()
        log.info("Capture worker stopped cleanly")


def main() -> None:
    try:
        asyncio.run(run_worker())
    except Exception:
        log.exception("Capture worker startup or runtime failure")
        raise


if __name__ == "__main__":
    main()
