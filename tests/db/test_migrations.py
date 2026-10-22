"""Database-independent checks for PostgreSQL migration DDL."""
from __future__ import annotations

import subprocess
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic(db_url: str, *args: str) -> subprocess.CompletedProcess:
    cmd = [
        str(REPO_ROOT / ".venv" / "bin" / "python"),
        "-m",
        "alembic",
        "-x",
        f"db_url={db_url}",
        *args,
    ]
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)


def test_alembic_postgresql_ddl_emits_cleanly():
    """Offline PostgreSQL DDL includes tables and indexes from the current chain."""
    proc = _run_alembic(
        "postgresql+asyncpg://u:p@localhost:5432/personal_os",
        "upgrade",
        "head",
        "--sql",
    )
    assert proc.returncode == 0, proc.stderr
    sql = proc.stdout
    for table in (
        "users",
        "projects",
        "tasks",
        "captures",
        "capture_processing",
        "capture_dispatch_outbox",
        "devices",
        "device_operations",
        "sync_operation_receipts",
        "sync_entity_revisions",
        "sync_conflicts",
        "sync_change_events",
        "sync_tombstones",
        "sync_feed_cursors",
        "conversations",
        "conversation_messages",
    ):
        assert f"CREATE TABLE {table} " in sql, table
    assert "uq_devices_device_id" in sql
    assert "ix_devices_owner_status" in sql
    assert "COMMIT" in sql


def test_alembic_revision_graph_has_one_current_head():
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)
    assert len(script.get_heads()) == 1
