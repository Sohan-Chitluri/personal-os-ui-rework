"""Tests for CompanionDaemon lifecycle and reconnect behavior (M2-A)."""

import asyncio
from pathlib import Path

import pytest

from personal_os.companion.config import CompanionSettings
from personal_os.companion.daemon import CompanionDaemon
from personal_os.companion.protocol import PresenceState


@pytest.mark.asyncio
async def test_daemon_startup_and_clean_shutdown(tmp_path: Path):
    """Daemon initializes identity, starts, and cleanly stops."""
    settings = CompanionSettings(
        config_dir=tmp_path / "companion",
        authorized_roots=[tmp_path],
        ws_url="ws://127.0.0.1:59999/ws/companion",  # Unused port
        auth_token="test-token",
        reconnect_min_backoff=0.01,
        reconnect_max_backoff=0.05,
    )
    daemon = CompanionDaemon(settings)
    assert daemon.presence == PresenceState.OFFLINE
    assert not daemon.is_running

    # Run start in a background task
    start_task = asyncio.create_task(daemon.start())

    # Wait briefly for start to engage
    await asyncio.sleep(0.05)
    assert daemon.is_running

    # Clean shutdown
    await daemon.stop()
    await asyncio.sleep(0.02)
    start_task.cancel()
    await asyncio.gather(start_task, return_exceptions=True)

    assert not daemon.is_running
    assert daemon.presence == PresenceState.OFFLINE


@pytest.mark.asyncio
async def test_daemon_backoff_increases_on_failure(tmp_path: Path):
    """Backoff increases exponentially up to reconnect_max_backoff."""
    settings = CompanionSettings(
        config_dir=tmp_path / "companion",
        reconnect_min_backoff=0.1,
        reconnect_max_backoff=0.4,
        reconnect_jitter=0.0,
    )
    daemon = CompanionDaemon(settings)
    assert daemon._current_backoff == 0.1

    await daemon._sleep_with_backoff()
    assert daemon._current_backoff == 0.2

    await daemon._sleep_with_backoff()
    assert daemon._current_backoff == 0.4

    # Capped at max_backoff
    await daemon._sleep_with_backoff()
    assert daemon._current_backoff == 0.4
