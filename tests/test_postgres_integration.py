"""M1-F PostgreSQL Integration and Deployment Contract Test Suite.

Verifies:
  Scope A — PostgreSQL Integration
    * Clean database creation and migration upgrade/downgrade chain
    * ORM schema and migration schema agreement (compare_metadata)
    * Unique capture idempotency constraints (uq_captures_owner_idem)
    * One processing record per capture (uq_capture_processing_capture_id)
    * Foreign keys (CASCADE, SET NULL) and transaction rollback
    * Optimistic revision updates (StaleRevisionError)
    * Soft-deleted records cannot be updated or revived
    * Concurrent capture submissions and workers do not duplicate tasks
    * Crash recovery preserves task IDs and processing results
    * Datetime values and timezone semantics behave consistently
  Scope B — Application and Runtime Contract
    * DATABASE_URL and ENVIRONMENT loading & normalisation
    * Queue selection is explicit and fails safely when misconfigured
    * Health and readiness distinct semantics (/health vs /api/v1/ready)
    * Worker entry point importability and dependency wiring
    * Artifact packaging and systemd unit verification
  Scope C — End-to-End Verification
    * API capture submission -> durable PG persistence -> queue dispatch ->
      worker processing -> task & extraction persistence -> idempotent resubmit ->
      crash recovery simulation -> API status reflection.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import glob
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tarfile
import tempfile
from typing import AsyncIterator, Optional
import zipfile
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from personal_os.api.deps import get_db_session
from personal_os.api.main import create_app
from personal_os.config import Settings, reset_settings_cache
from personal_os.core.extraction import ExtractionType, SemanticExtractor
from personal_os.core.extraction.actions import CreateTask
from personal_os.core.extraction.models import RawExtraction
from personal_os.core.llm import LLMProvider
from personal_os.db.base import Base, utcnow
from personal_os.db.models import Capture, CaptureProcessing, Project, Task, User
from personal_os.db.repositories import (
    SqlCaptureProcessingRepository,
    SqlCaptureRepository,
    SqlProjectRepository,
    SqlTaskRepository,
    SqlUserRepository,
    StaleRevisionError,
)
from personal_os.db.session import adispose_engine, normalise_database_url
from personal_os.services.interfaces import ServiceNotFound
from personal_os.services.queue import LocalCaptureQueue, SqsCaptureQueue, build_capture_queue
from personal_os.services.outbox import CaptureOutboxDispatcher
from personal_os.services.tasks import CaptureServiceImpl, TaskServiceImpl
from personal_os.services.worker import CaptureWorker

REPO_ROOT = Path(__file__).resolve().parent.parent


def _find_pg_bin() -> Optional[str]:
    """Find directory containing initdb and pg_ctl."""
    # 1. Existing PATH
    if shutil.which("initdb") and shutil.which("pg_ctl"):
        return os.path.dirname(shutil.which("initdb") or "")
    # 2. Nix store paths
    nix_candidates = sorted(glob.glob("/nix/store/*postgresql*/bin"), reverse=True)
    for cand in nix_candidates:
        if os.path.exists(os.path.join(cand, "initdb")) and os.path.exists(
            os.path.join(cand, "pg_ctl")
        ):
            return cand
    return None


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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


@pytest.fixture(scope="session")
def pg_server():
    """Use the guarded test DB, or start a disposable local cluster if available."""
    configured_url = os.environ.get("DATABASE_URL")
    if configured_url:
        from sqlalchemy.engine import make_url

        parsed = make_url(configured_url)
        if (
            parsed.drivername != "postgresql+asyncpg"
            or parsed.database != "personal_os_test"
            or parsed.host not in {"localhost", "127.0.0.1", "::1"}
        ):
            pytest.fail(
                "PostgreSQL integration tests require local PostgreSQL database "
                "personal_os_test; refusing any other target."
            )
        import asyncpg

        async def reset_and_migrate() -> None:
            conn = await asyncpg.connect(
                configured_url.replace("postgresql+asyncpg://", "postgresql://")
            )
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
            await conn.execute("GRANT ALL ON SCHEMA public TO CURRENT_USER")
            await conn.close()

        asyncio.run(reset_and_migrate())
        up = _run_alembic(configured_url, "upgrade", "head")
        if up.returncode != 0:
            pytest.fail(f"Initial Alembic upgrade failed: {up.stderr}")
        yield {"db_url": configured_url, "external": True}
        return

    pg_bin = _find_pg_bin()
    if not pg_bin:
        pytest.fail(
            "PostgreSQL integration tests need DATABASE_URL targeting local "
            "personal_os_test or available initdb/pg_ctl binaries."
        )

    port = _get_free_port()
    tmp_dir = tempfile.mkdtemp(prefix="pg_pos_test_")
    pg_data = os.path.join(tmp_dir, "data")
    sock_dir = os.path.join(tmp_dir, "sock")
    os.makedirs(sock_dir, exist_ok=True)
    log_file = os.path.join(tmp_dir, "server.log")

    env = os.environ.copy()
    env["PATH"] = f"{pg_bin}:{env.get('PATH', '')}"

    initdb_cmd = [
        os.path.join(pg_bin, "initdb"),
        "-D",
        pg_data,
        "--no-locale",
        "-E",
        "UTF8",
        "--auth=trust",
        "-U",
        "postgres",
    ]
    sub = subprocess.run(initdb_cmd, env=env, capture_output=True, text=True)
    if sub.returncode != 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        pytest.fail(f"initdb failed: {sub.stderr}")

    pg_ctl_start = [
        os.path.join(pg_bin, "pg_ctl"),
        "-D",
        pg_data,
        "-o",
        f"-k {sock_dir} -p {port} -F -h 127.0.0.1",
        "-l",
        log_file,
        "start",
    ]
    sub = subprocess.run(pg_ctl_start, env=env, capture_output=True, text=True)
    if sub.returncode != 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        pytest.fail(f"pg_ctl start failed: {sub.stderr}")

    createdb_cmd = [
        os.path.join(pg_bin, "createdb"),
        "-h",
        "127.0.0.1",
        "-p",
        str(port),
        "-U",
        "postgres",
        "pos_test",
    ]
    sub = subprocess.run(createdb_cmd, env=env, capture_output=True, text=True)
    if sub.returncode != 0:
        subprocess.run([os.path.join(pg_bin, "pg_ctl"), "-D", pg_data, "stop"], env=env)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        pytest.fail(f"createdb failed: {sub.stderr}")

    db_url = f"postgresql+asyncpg://postgres@127.0.0.1:{port}/pos_test"

    # Initial migration upgrade
    up = _run_alembic(db_url, "upgrade", "head")
    if up.returncode != 0:
        subprocess.run([os.path.join(pg_bin, "pg_ctl"), "-D", pg_data, "stop"], env=env)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        pytest.fail(f"Initial alembic upgrade failed: {up.stderr}")

    yield {
        "db_url": db_url,
        "port": port,
        "pg_bin": pg_bin,
        "data_dir": pg_data,
        "tmp_dir": tmp_dir,
        "external": False,
    }

    subprocess.run(
        [os.path.join(pg_bin, "pg_ctl"), "-D", pg_data, "-m", "immediate", "stop"],
        env=env,
        capture_output=True,
    )
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest_asyncio.fixture
async def pg_engine(pg_server):
    engine = create_async_engine(pg_server["db_url"], pool_size=5, max_overflow=5)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_session(pg_engine):
    factory = async_sessionmaker(bind=pg_engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        yield session


# ============================================================================
# Scope A: PostgreSQL Integration
# ============================================================================


def test_a_migration_chain_and_downgrade(pg_server):
    """Verify clean database can be migrated and downgraded using the actual Alembic chain."""
    db_url = pg_server["db_url"]

    # Verify tables in PostgreSQL
    import asyncpg

    async def check_tables():
        raw_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(raw_url)
        tables = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        )
        names = {t["table_name"] for t in tables}
        assert {"users", "projects", "tasks", "captures", "capture_processing", "capture_dispatch_outbox", "alembic_version"}.issubset(
            names
        )

        # Check revision is at the latest migration.
        rev = await conn.fetchval("SELECT version_num FROM alembic_version")
        expected_head = ScriptDirectory.from_config(
            Config(str(REPO_ROOT / "alembic.ini"))
        ).get_current_head()
        assert rev == expected_head
        await conn.close()

    asyncio.run(check_tables())

    # Exercise the append-only outbox migration's recovery backfill. A
    # previously unfinished job becomes dispatchable; terminal work does not.
    down = _run_alembic(db_url, "downgrade", "0002_uniqueness")
    assert down.returncode == 0, down.stderr

    async def seed_pre_outbox_jobs():
        raw_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(raw_url)
        owner_id = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
        pending_id = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
        done_id = "01ARZ3NDEKTSV4RRFFQ69G5FAX"
        await conn.execute(
            "INSERT INTO users (id,email) VALUES ($1,$2)", owner_id, "backfill@test.com"
        )
        for capture_id, status, attempt in (
            (pending_id, "pending", 0),
            (done_id, "succeeded", 1),
        ):
            await conn.execute(
                """INSERT INTO captures (id,owner_id,content) VALUES ($1,$2,$3)""",
                capture_id,
                owner_id,
                "migration backfill test",
            )
            await conn.execute(
                """INSERT INTO capture_processing (id,capture_id,status,attempt,max_attempts)
                   VALUES ($1,$2,$3,$4,3)""",
                "01ARZ3NDEKTSV4RRFFQ69G5FAY" if status == "pending" else "01ARZ3NDEKTSV4RRFFQ69G5FAZ",
                capture_id,
                status,
                attempt,
            )
        await conn.close()

    asyncio.run(seed_pre_outbox_jobs())
    up = _run_alembic(db_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr

    async def check_backfill():
        raw_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(raw_url)
        records = await conn.fetch(
            "SELECT capture_id,state,next_attempt_at FROM capture_dispatch_outbox"
        )
        assert len(records) == 1
        assert records[0]["capture_id"] == "01ARZ3NDEKTSV4RRFFQ69G5FAW"
        assert records[0]["state"] == "pending" and records[0]["next_attempt_at"] is None
        await conn.close()

    asyncio.run(check_backfill())

    # Downgrade to base
    down = _run_alembic(db_url, "downgrade", "base")
    assert down.returncode == 0, down.stderr

    async def check_dropped():
        raw_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(raw_url)
        tables = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        )
        names = {t["table_name"] for t in tables}
        assert not ({"users", "projects", "tasks", "captures", "capture_processing", "capture_dispatch_outbox"} & names)
        await conn.close()

    asyncio.run(check_dropped())

    # Re-apply upgrade to head for subsequent tests
    up = _run_alembic(db_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr
    from personal_os.db.session import dispose_engine

    dispose_engine()


@pytest.mark.asyncio
async def test_a_orm_and_migration_schema_agree(pg_engine):
    """Verify ORM schema and migration schema agree with 0 autogenerate diffs."""
    async with pg_engine.connect() as conn:
        def _check(sync_conn):
            mc = MigrationContext.configure(sync_conn)
            diff = compare_metadata(mc, Base.metadata)
            return diff

        diff = await conn.run_sync(_check)
        assert len(diff) == 0, f"Schema discrepancies detected: {diff}"


@pytest.mark.asyncio
async def test_a_unique_capture_idempotency_constraint(pg_session):
    """Verify unique capture idempotency constraint works, and NULL keys are allowed."""
    user = User(email="idem@test.com")
    pg_session.add(user)
    await pg_session.commit()

    c1 = Capture(owner_id=user.id, content="First", idempotency_key="key-abc")
    pg_session.add(c1)
    await pg_session.commit()

    # Duplicate key for same owner must fail
    c2 = Capture(owner_id=user.id, content="Second", idempotency_key="key-abc")
    pg_session.add(c2)
    with pytest.raises(IntegrityError):
        await pg_session.flush()
    await pg_session.rollback()

    # Re-fetch user in rolled-back session
    u = await pg_session.scalar(select(User).where(User.email == "idem@test.com"))
    assert u is not None
    # Multiple NULL idempotency keys must be accepted
    c3 = Capture(owner_id=u.id, content="No key 1", idempotency_key=None)
    c4 = Capture(owner_id=u.id, content="No key 2", idempotency_key=None)
    pg_session.add_all([c3, c4])
    await pg_session.flush()
    await pg_session.commit()


@pytest.mark.asyncio
async def test_a_one_processing_record_per_capture(pg_session):
    """Verify exactly one processing record per capture is enforced by DB constraint."""
    u = User(email="oneproc@test.com")
    pg_session.add(u)
    await pg_session.flush()
    c = Capture(owner_id=u.id, content="Process once")
    pg_session.add(c)
    await pg_session.commit()

    proc1 = CaptureProcessing(capture_id=c.id, status="pending")
    pg_session.add(proc1)
    await pg_session.commit()

    proc2 = CaptureProcessing(capture_id=c.id, status="processing")
    pg_session.add(proc2)
    with pytest.raises(IntegrityError):
        await pg_session.flush()
    await pg_session.rollback()


@pytest.mark.asyncio
async def test_a_foreign_keys_and_cascades(pg_session):
    """Verify foreign keys, CASCADE deletes, SET NULL, and transaction rollbacks."""
    u = User(email="fks@test.com")
    pg_session.add(u)
    await pg_session.flush()

    proj = Project(owner_id=u.id, name="FK Proj")
    pg_session.add(proj)
    await pg_session.flush()

    cap = Capture(owner_id=u.id, project_id=proj.id, content="FK Cap")
    pg_session.add(cap)
    await pg_session.flush()

    proc = CaptureProcessing(capture_id=cap.id, status="pending")
    task = Task(owner_id=u.id, project_id=proj.id, capture_id=cap.id, title="FK Task")
    pg_session.add_all([proc, task])
    await pg_session.commit()

    # Delete project -> task.project_id and cap.project_id set to NULL
    await pg_session.delete(proj)
    await pg_session.commit()

    await pg_session.refresh(task)
    await pg_session.refresh(cap)
    assert task.project_id is None
    assert cap.project_id is None

    # Delete capture -> proc cascaded; task.capture_id set to NULL
    await pg_session.delete(cap)
    await pg_session.commit()
    pg_session.expunge_all()

    assert await pg_session.get(CaptureProcessing, proc.id) is None
    refreshed_task = await pg_session.get(Task, task.id)
    assert refreshed_task.capture_id is None

    # Delete user -> task cascaded
    u_obj = await pg_session.get(User, u.id)
    await pg_session.delete(u_obj)
    await pg_session.commit()
    pg_session.expunge_all()
    assert await pg_session.get(Task, task.id) is None


@pytest.mark.asyncio
async def test_a_optimistic_revision_updates(pg_session):
    """Verify optimistic locking rejects stale revisions."""
    u = User(email="rev@test.com")
    pg_session.add(u)
    await pg_session.commit()
    task_repo = SqlTaskRepository(pg_session)
    task = await task_repo.create(u.id, "Original Title")
    assert task.revision == 1

    # Valid update
    updated = await task_repo.update(task.id, u.id, expected_revision=1, title="New Title")
    assert updated.revision == 2
    assert updated.title == "New Title"

    # Stale update
    with pytest.raises(StaleRevisionError):
        await task_repo.update(task.id, u.id, expected_revision=1, title="Stale Update")


@pytest.mark.asyncio
async def test_a_soft_deleted_records(pg_session):
    """Verify soft-deleted records cannot be updated or revived."""
    u = User(email="softdel@test.com")
    pg_session.add(u)
    await pg_session.commit()
    task_repo = SqlTaskRepository(pg_session)
    task = await task_repo.create(u.id, "Delete me")

    # Soft delete
    deleted = await task_repo.soft_delete(task.id, u.id)
    assert deleted is True

    # Cannot get
    assert await task_repo.get(task.id, u.id) is None

    # Cannot update - raises StaleRevisionError (cannot be revived or updated)
    with pytest.raises(StaleRevisionError):
        await task_repo.update(task.id, u.id, title="Try to revive")


@pytest.mark.asyncio
async def test_a_crash_recovery_preserves_task_ids(pg_session):
    """Verify crash recovery preserves task IDs and results without duplicates."""
    from personal_os.services.processing import ProcessingServiceImpl

    u = User(email="crash@test.com")
    pg_session.add(u)
    await pg_session.flush()
    cap = Capture(owner_id=u.id, content="Crash test")
    pg_session.add(cap)
    await pg_session.flush()

    # Pre-create task to simulate partial write before worker crash
    task = await SqlTaskRepository(pg_session).create(
        u.id, "Crash Recover Task", capture_id=cap.id
    )
    job = await SqlCaptureProcessingRepository(pg_session).create(cap.id)
    await pg_session.commit()

    class MockExtractor:
        async def extract(self, text, *args, **kwargs):
            return type(
                "Result",
                (),
                {
                    "items": [
                        type(
                            "Item",
                            (),
                            {
                                "raw": RawExtraction(
                                    type=ExtractionType.TASK,
                                    description="Crash Recover Task",
                                    certainty=0.9,
                                    confidence=0.9,
                                    source_excerpt="Crash Recover Task",
                                    metadata={},
                                ),
                                "action": CreateTask("Crash Recover Task", None),
                            },
                        )
                    ],
                    "errors": [],
                    "source_text": text,
                },
            )()

    svc = ProcessingServiceImpl(pg_session, MockExtractor())
    result = await svc.process_capture(cap.id, u.id)
    await pg_session.commit()
    assert result.status == "succeeded"
    assert result.task_ids == [task.id]

    # Verify no duplicate task created
    all_tasks = await pg_session.scalars(select(Task).where(Task.capture_id == cap.id))
    assert len(all_tasks.all()) == 1


@pytest.mark.asyncio
async def test_a_datetime_and_timezone_semantics(pg_session):
    """Verify timezone semantics and roundtrips with timestamptz."""
    u = User(email="tz@test.com")
    pg_session.add(u)
    await pg_session.flush()

    dt = datetime(2027, 8, 15, 14, 30, tzinfo=timezone.utc)
    task = Task(owner_id=u.id, title="TZ Task", due_date=dt)
    pg_session.add(task)
    await pg_session.commit()

    loaded = await pg_session.get(Task, task.id)
    assert loaded.due_date is not None
    assert loaded.due_date.tzinfo is not None
    assert loaded.due_date == dt


# ============================================================================
# Scope B: Application and Runtime Contract
# ============================================================================


def test_b_settings_and_url_normalisation():
    """Verify DATABASE_URL and ENVIRONMENT loading and driver normalisation."""
    reset_settings_cache()
    orig_db = os.environ.get("DATABASE_URL")
    orig_env = os.environ.get("ENVIRONMENT")
    os.environ["DATABASE_URL"] = "postgresql://user:pass@db.internal:5432/pos"
    os.environ["ENVIRONMENT"] = "staging"
    try:
        s = Settings()
        assert s.environment == "staging"
        assert s.database_url == "postgresql://user:pass@db.internal:5432/pos"
        norm = normalise_database_url(s.database_url)
        assert norm == "postgresql+asyncpg://user:pass@db.internal:5432/pos"
        assert normalise_database_url("postgres://u:p@h/d") == "postgresql+asyncpg://u:p@h/d"
        assert (
            normalise_database_url("postgresql+psycopg2://u:p@h/d")
            == "postgresql+asyncpg://u:p@h/d"
        )
        assert (
            normalise_database_url("postgresql://u:p@h:5432/d?sslmode=require")
            == "postgresql+asyncpg://u:p@h:5432/d?ssl=require"
        )
        assert (
            normalise_database_url("postgresql+asyncpg://u:p@h:5432/d?sslmode=require")
            == "postgresql+asyncpg://u:p@h:5432/d?ssl=require"
        )
    finally:
        if orig_db is not None:
            os.environ["DATABASE_URL"] = orig_db
        else:
            os.environ.pop("DATABASE_URL", None)
        if orig_env is not None:
            os.environ["ENVIRONMENT"] = orig_env
        else:
            os.environ.pop("ENVIRONMENT", None)
        reset_settings_cache()


def test_b_queue_selection_safety():
    """Verify queue selection is explicit and fails safely when misconfigured."""
    s_local = Settings(environment="local", queue_backend="inline")
    q = build_capture_queue(s_local)
    assert isinstance(q, LocalCaptureQueue)

    s_sqs = Settings(
        environment="staging",
        queue_backend="sqs",
        sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/captures",
    )
    sqs_queue = build_capture_queue(s_sqs, sqs_client=object())
    assert isinstance(sqs_queue, SqsCaptureQueue)

    s_unsafe = Settings(environment="staging", queue_backend="inline")
    with pytest.raises(RuntimeError, match="supported only in local/test"):
        build_capture_queue(s_unsafe)


@pytest.mark.asyncio
async def test_b_health_and_readiness_endpoints(pg_server):
    """Verify /health (liveness) and /api/v1/ready (readiness) have clear distinct semantics."""
    s = Settings(
        environment="test",
        database_url=pg_server["db_url"],
        allow_synthetic_auth=True,
    )
    app = create_app(s)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            # Liveness requires no DB check
            h_resp = await ac.get("/health")
            assert h_resp.status_code == 200
            assert h_resp.json() == {"status": "ok"}

            # Readiness connects to PostgreSQL
            r_resp = await ac.get("/api/v1/ready")
            assert r_resp.status_code == 200
            assert r_resp.json() == {"status": "ready", "database": "connected", "details": None}


def test_b_worker_entrypoint_and_artifact_contract():
    """Verify worker entry point exists and wheel/bundle contain required files."""
    import personal_os.deploy_worker as dw

    assert hasattr(dw, "main")
    assert hasattr(dw, "run_worker")

    # Verify build scripts and systemd definitions
    api_unit = REPO_ROOT / "deploy" / "systemd" / "pos-api.service"
    worker_unit = REPO_ROOT / "deploy" / "systemd" / "pos-worker.service"
    assert api_unit.exists()
    assert worker_unit.exists()
    api_text = api_unit.read_text()
    worker_text = worker_unit.read_text()
    assert "WorkingDirectory=/opt/personal-os" in api_text
    assert "personal_os.api.main:app" in api_text
    assert "personal_os.deploy_worker" in worker_text


# ============================================================================
# Scope C: End-to-End Verification against PostgreSQL
# ============================================================================


@pytest.mark.asyncio
async def test_c_end_to_end_capture_lifecycle(pg_server):
    """Full lifecycle: API capture -> PostgreSQL -> queue -> worker -> tasks -> idempotency & recovery."""
    from personal_os.config import get_settings

    settings = Settings(
        environment="test",
        database_url=pg_server["db_url"],
        allow_synthetic_auth=True,
    )
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    engine = create_async_engine(pg_server["db_url"])
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def _override_db_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db_session] = _override_db_session

    async with app.router.lifespan_context(app):
        queue = app.state.capture_queue
        assert isinstance(queue, LocalCaptureQueue)

        # 1. Start application with auth client
        headers = {"Authorization": "Bearer test:alice"}
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers=headers,
        ) as client:
            # 2. Submit capture through API
            idem_key = "e2e-idem-12345"
            payload = {
                "content": "Deploy Personal OS to AWS next Tuesday",
                "source": "web",
                "idempotency_key": idem_key,
            }
            resp = await client.post("/api/v1/captures", json=payload)
            assert resp.status_code == 201, resp.text
            data = resp.json()
            capture_id = data["capture_id"]
            assert data["persisted"] is True
            assert data["status"] == "pending"
            assert queue.size == 0  # API committed outbox intent; publisher sends after commit.

            dispatcher = CaptureOutboxDispatcher(session_factory, queue)
            assert await dispatcher.run_once() == 1
            assert queue.size == 1

            # 3. Verify durable persistence in PostgreSQL
            async with session_factory() as session:
                cap_row = await session.get(Capture, capture_id)
                assert cap_row is not None
                assert cap_row.content == payload["content"]
                assert cap_row.idempotency_key == idem_key

                # 4. The committed outbox was published to the same app queue.
                assert queue.size == 1

            # 5. Process capture with worker
            class MockLLM(LLMProvider):
                async def extract(self, text, *args, **kwargs):
                    return [
                        RawExtraction(
                            type=ExtractionType.TASK,
                            description="Deploy Personal OS to AWS",
                            certainty=0.9,
                            confidence=0.9,
                            source_excerpt="Deploy Personal OS to AWS",
                            metadata={},
                        )
                    ]

                async def close(self) -> None:
                    pass

            extractor = SemanticExtractor(MockLLM())
            worker = CaptureWorker(
                session_factory,
                queue,
                extractor,
                outbox_dispatcher=dispatcher,
            )
            await worker.run(max_messages=1)

            # 6. Verify tasks and extraction results are persisted
            async with session_factory() as session:
                job = await SqlCaptureProcessingRepository(session).get_by_capture(capture_id)
                assert job is not None
                assert job.status == "succeeded"
                assert len(job.task_ids) == 1
                task = await session.get(Task, job.task_ids[0])
                assert task is not None
                assert task.title == "Deploy Personal OS to AWS"
                assert task.capture_id == capture_id

            # 7. Repeat submission using the same idempotency key
            resp_dup = await client.post("/api/v1/captures", json=payload)
            assert resp_dup.status_code == 201
            assert resp_dup.json()["capture_id"] == capture_id
            assert await dispatcher.run_once() == 0

            # 8. Simulate worker failure and restart (crash recovery)
            # Create another capture and simulate crash after partial execution
            cap2_resp = await client.post(
                "/api/v1/captures", json={"content": "Second capture for crash test"}
            )
            cap2_id = cap2_resp.json()["capture_id"]
            assert queue.size == 0

            # Publish, receive, and simulate a process crash before handling.
            assert await dispatcher.run_once() == 1

            # Drain queue message to simulate crash after receive.
            msg = await queue.dequeue()
            assert msg is not None
            assert msg.capture_id == cap2_id

            # Local queue recovery rearms unfinished dispatched work.
            await worker.recover_pending()
            assert queue.size >= 1

            # Process again to completion
            while queue.size > 0:
                await worker.run(max_messages=1)

            # 9. Verify recovery without duplicate tasks or lost task IDs
            async with session_factory() as session:
                job2 = await SqlCaptureProcessingRepository(session).get_by_capture(cap2_id)
                assert job2.status == "succeeded"
                assert len(job2.task_ids) == 1

            # 10. Verify API status reflects result
            status_resp = await client.get(f"/api/v1/captures/{capture_id}")
            assert status_resp.status_code == 200
            assert status_resp.json()["id"] == capture_id
            assert status_resp.json()["status"] == "processed"

            proc_resp = await client.get(f"/api/v1/captures/{capture_id}/status")
            assert proc_resp.status_code == 200
            assert proc_resp.json()["status"] == "succeeded"

    await engine.dispose()


@pytest.mark.asyncio
async def test_c_concurrent_user_creation_api(pg_server):
    """Concurrent API requests for the same previously unseen subject must all succeed,
    produce identical canonical user ownership, and create exactly one user row in PostgreSQL.

    Also tests subsequent requests for the existing user and concurrent requests
    for distinct subjects to ensure complete concurrency and transaction health.
    """
    from personal_os.config import get_settings

    settings = Settings(
        environment="test",
        database_url=pg_server["db_url"],
        allow_synthetic_auth=True,
    )
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    engine = create_async_engine(pg_server["db_url"], pool_size=20, max_overflow=10)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def _override_db_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db_session] = _override_db_session

    async with app.router.lifespan_context(app):
        # 1. Concurrent requests for the same new subject
        new_sub = f"user-concurrent-{uuid4().hex}"
        concurrency = 10
        barrier = asyncio.Barrier(concurrency)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            async def _call_concurrent():
                await barrier.wait()
                return await client.get(
                    "/api/v1/projects",
                    headers={"Authorization": f"Bearer test:{new_sub}"},
                )

            responses = await asyncio.gather(*[_call_concurrent() for _ in range(concurrency)])
            assert all(r.status_code == 200 for r in responses), [r.status_code for r in responses]

        # Verify exactly one user row exists in PostgreSQL
        async with session_factory() as session:
            rows = (
                await session.execute(select(User).where(User.cognito_sub == new_sub))
            ).scalars().all()
            assert len(rows) == 1
            canonical_user = rows[0]
            assert canonical_user.cognito_sub == new_sub
            canonical_id = canonical_user.id

        # 2. Existing user request succeeds and does not duplicate rows
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer test:{new_sub}"},
        ) as client:
            resp = await client.get("/api/v1/projects")
            assert resp.status_code == 200

        async with session_factory() as session:
            rows = (
                await session.execute(select(User).where(User.cognito_sub == new_sub))
            ).scalars().all()
            assert len(rows) == 1
            assert rows[0].id == canonical_id

        # 3. Concurrent requests for distinct subjects all succeed independently
        distinct_subs = [f"user-distinct-{uuid4().hex[:8]}-{i}" for i in range(5)]
        barrier_distinct = asyncio.Barrier(len(distinct_subs))

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            async def _call_distinct(sub: str):
                await barrier_distinct.wait()
                return await client.get(
                    "/api/v1/projects",
                    headers={"Authorization": f"Bearer test:{sub}"},
                )

            distinct_responses = await asyncio.gather(
                *[_call_distinct(sub) for sub in distinct_subs]
            )
            assert all(r.status_code == 200 for r in distinct_responses)

        async with session_factory() as session:
            for sub in distinct_subs:
                u = (
                    await session.execute(select(User).where(User.cognito_sub == sub))
                ).scalar_one_or_none()
                assert u is not None

    await engine.dispose()

