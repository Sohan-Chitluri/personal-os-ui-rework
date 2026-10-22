"""Tests for ProcessInspector and secret redaction (M2-A)."""

import os

from personal_os.companion.process import ProcessInspector, sanitize_process_name
from personal_os.companion.protocol import ProcessListParams


def test_sanitize_process_name_redacts_credentials():
    """Secrets in command strings are redacted."""
    dirty = "my_app --token=secret123 --bearer=xyz789 --password=mysecretpass123"
    clean = sanitize_process_name(dirty)
    assert "secret123" not in clean
    assert "xyz789" not in clean
    assert "mysecretpass123" not in clean
    assert "token=[REDACTED]" in clean
    assert "bearer=[REDACTED]" in clean
    assert "password=[REDACTED]" in clean


def test_process_inspector_reports_current_user_process():
    """Inspector finds the current running test process."""
    inspector = ProcessInspector()
    current_pid = os.getpid()

    result = inspector.list_processes(ProcessListParams(limit=200))
    assert result.total > 0
    pids = {p.pid for p in result.processes}
    assert current_pid in pids

    # Find self
    current_p = next(p for p in result.processes if p.pid == current_pid)
    assert current_p.status == "running"
    assert current_p.name  # non-empty


def test_process_inspector_respects_limit():
    """Process inspection respects the limit parameter."""
    inspector = ProcessInspector()
    result = inspector.list_processes(ProcessListParams(limit=3))
    assert len(result.processes) <= 3
