"""Model + migration tests: dual-dialect compilation, constraints, triggers."""
from __future__ import annotations

from sqlalchemy.schema import CreateTable, CreateIndex
from sqlalchemy.dialects import postgresql, sqlite

from personal_os.db import models as m
from personal_os.db.base import Base, new_ulid


def _all_tables():
    return [
        m.User.__table__,
        m.Project.__table__,
        m.Task.__table__,
        m.Capture.__table__,
        m.CaptureProcessing.__table__,
        m.CaptureDispatchOutbox.__table__,
        m.Device.__table__,
    ]


def test_all_tables_compile_on_both_dialects():
    for tbl in _all_tables():
        for dialect in (postgresql.dialect(), sqlite.dialect()):
            ddl = str(CreateTable(tbl).compile(dialect=dialect))
            assert tbl.name in ddl


def test_all_indexes_compile_on_both_dialects():
    for tbl in _all_tables():
        for idx in tbl.indexes:
            for dialect in (postgresql.dialect(), sqlite.dialect()):
                str(CreateIndex(idx).compile(dialect=dialect))


def test_ulid_primary_keys_are_26_char_strings():
    for tbl in _all_tables():
        id_col = tbl.columns["id"]
        assert id_col.primary_key
        assert str(id_col.type) in {"CHAR(26)", "VARCHAR(26)"}


def test_revision_column_exists_on_mutable_records():
    for tbl in (m.Project.__table__, m.Task.__table__, m.Capture.__table__, m.CaptureProcessing.__table__):
        assert "revision" in tbl.columns


def test_capture_idempotency_index_exists():
    tbl = m.Capture.__table__
    idx_names = {i.name for i in tbl.indexes}
    assert "ix_captures_owner_idem" in idx_names


def test_capture_processing_cascades_on_capture_delete():
    fk = list(m.CaptureProcessing.__table__.columns["capture_id"].foreign_keys)[0]
    assert fk.constraint.ondelete == "CASCADE"


def test_task_capture_fk_set_null():
    fk = list(m.Task.__table__.columns["capture_id"].foreign_keys)[0]
    assert fk.constraint.ondelete == "SET NULL"


def test_new_ulid_is_valid():
    u = new_ulid()
    assert len(u) == 26
    assert u.isascii() and u.isalnum()


def test_sqlite_application_database_url_is_rejected():
    import pytest

    from personal_os.config import Settings
    from personal_os.db.session import build_engine

    with pytest.raises(ValueError, match="SQLite is unsupported"):
        build_engine(Settings(database_url="sqlite:///unused.db"))


async def test_create_and_read_roundtrip_postgresql(db_session):
    from personal_os.db.models import (
        User,
        Project,
        Task,
        Capture,
        CaptureProcessing,
        CaptureDispatchOutbox,
    )

    user = User(email="roundtrip@example.com")
    db_session.add(user)
    await db_session.flush()

    proj = Project(owner_id=user.id, name="Test Project")
    db_session.add(proj)
    await db_session.flush()

    task = Task(owner_id=user.id, project_id=proj.id, title="Write tests")
    db_session.add(task)

    cap = Capture(owner_id=user.id, content="hello world", source="cli")
    db_session.add(cap)
    await db_session.flush()

    proc = CaptureProcessing(capture_id=cap.id, status="pending")
    db_session.add(proc)
    outbox = CaptureDispatchOutbox(capture_id=cap.id, state="pending")
    db_session.add(outbox)
    await db_session.flush()

    assert task.revision == 1
    assert cap.revision == 1
    assert proc.status == "pending"
    assert outbox.state == "pending" and outbox.attempts == 0
    assert len(user.id) == 26 and len(proj.id) == 26 and len(task.id) == 26
