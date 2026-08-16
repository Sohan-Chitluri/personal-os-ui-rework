"""Capture contract - the single guaranteed entry path for Personal OS.

Every input path (text, voice, CLI) MUST go through this module. The contract is:

    Capture -> Persist (append_journal_entry, timestamped, atomic, committed)
            -> Queue processing (async, non-blocking)
            -> Update vault -> UI feedback

Persistence happens FIRST and is guaranteed. Processing is best-effort and
decoupled: a raw capture is NEVER lost even if extraction/processing fails.

This module is the fix for the v0.2 blocking finding: the TUI previously called
process_entry() without ever persisting the raw message to the Daily Stream.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import ulid as _ulid

from personal_os.core.vault import get_vault, VaultManager
from personal_os.core.processor import (
    BackgroundProcessor,
    ProcessingResult,
    process_entry,
)

logger = logging.getLogger(__name__)


@dataclass
class CaptureReceipt:
    """Proof that a raw capture was durably persisted.

    Returned synchronously the moment persistence succeeds, before any
    processing runs. The presence of a receipt with ``persisted=True`` means
    the raw text is safe on disk and committed to git.

    capture_id is a ULID embedded in the journal entry header and shared with
    the semantic block — the stable traceability key between raw capture and
    semantic interpretation.
    """

    raw: str
    source: str
    timestamp: datetime
    journal_path: Optional[Path] = None
    persisted: bool = False
    error: Optional[str] = None
    capture_id: str = ""


def persist_capture(
    raw: str,
    source: str = "Text",
    timestamp: Optional[datetime] = None,
    vault: Optional[VaultManager] = None,
) -> CaptureReceipt:
    """Persist a raw capture to the Daily Stream. STAGE 1 — always first.

    Durable, atomic, git-committed via VaultManager.append_journal_entry.
    Raises nothing to the caller for a normal failure; instead returns a
    receipt with ``persisted=False`` and an ``error`` so the UI can decide how
    to surface it. The raw text is preserved by definition on success.
    """
    vault = vault or get_vault()
    timestamp = timestamp or datetime.now()
    raw = (raw or "").strip()

    if not raw:
        return CaptureReceipt(
            raw=raw, source=source, timestamp=timestamp,
            persisted=False, error="empty capture",
        )

    capture_id = str(_ulid.ulid())
    try:
        path = vault.append_journal_entry(raw, timestamp=timestamp, source=source, capture_id=capture_id)
        logger.info("Captured (%s) cid=%s -> %s", source, capture_id[:8], path)
        return CaptureReceipt(
            raw=raw, source=source, timestamp=timestamp,
            journal_path=path, persisted=True, capture_id=capture_id,
        )
    except Exception as e:  # noqa: BLE001 - capture must degrade gracefully
        logger.error("Capture persistence FAILED (%s): %s", source, e)
        return CaptureReceipt(
            raw=raw, source=source, timestamp=timestamp,
            persisted=False, error=str(e),
        )


async def capture(
    raw: str,
    source: str = "Text",
    *,
    processor: Optional[BackgroundProcessor] = None,
    vault: Optional[VaultManager] = None,
    process: bool = True,
) -> CaptureReceipt:
    """Full capture contract: persist first, then queue/run processing.

    - Persistence (stage 1) is synchronous and guaranteed before we return.
    - Processing (stage 2) is decoupled:
        * If a ``processor`` (BackgroundProcessor) is supplied, the entry is
          QUEUED for non-blocking background processing and we return the
          receipt immediately. UI feedback for processing arrives later.
        * If no processor is supplied and ``process=True``, we await a one-shot
          process_entry (used by the CLI, which has no long-lived worker).
    - If persistence fails, processing is skipped entirely and the receipt
      carries the error. The raw text is never silently dropped.
    """
    receipt = persist_capture(raw, source=source, vault=vault)

    if not receipt.persisted:
        return receipt

    if not process:
        return receipt

    try:
        if processor is not None:
            # Non-blocking: hand off to the background worker.
            await processor.queue(receipt.raw, source)
        else:
            # One-shot path (CLI): process inline and discard the result here.
            await process_entry(receipt.raw, source)
    except Exception as e:  # noqa: BLE001 - processing failure must not lose capture
        logger.error("Processing enqueue/run failed (raw is safe): %s", e)

    return receipt


async def capture_and_process(
    raw: str,
    source: str = "Text",
    *,
    vault: Optional[VaultManager] = None,
) -> tuple[CaptureReceipt, Optional[ProcessingResult]]:
    """Persist first, then run processing inline and return BOTH results.

    Used where the caller wants the ProcessingResult synchronously (CLI
    ``pos note`` output, or a foreground TUI flow that shows changes). Raw is
    persisted before processing; a processing failure returns (receipt, None).
    """
    receipt = persist_capture(raw, source=source, vault=vault)
    if not receipt.persisted:
        return receipt, None

    try:
        result = await process_entry(receipt.raw, source)
        return receipt, result
    except Exception as e:  # noqa: BLE001
        logger.error("Processing failed (raw is safe at %s): %s", receipt.journal_path, e)
        return receipt, None
