"""SQLAlchemy implementation of the M1-A repository contract.

The production implementation uses PostgreSQL through SQLAlchemy Core/ORM.
Callers must pass an ``AsyncSession``; committing is the caller's
(service/unit-of-work) responsibility except where noted.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.companion.protocol import IDEMPOTENT_CAPABILITIES
from personal_os.db import models as m
from personal_os.db.base import new_ulid
from personal_os.sync.change_feed import append_entity_change


class _Repo:
    """Shared session handling and optimistic-lock guard."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _guarded_update(
        self,
        model: Any,
        record_id: str,
        owner_id: str | None,
        expected_revision: int | None,
        fields: dict,
    ) -> Any | None:
        """Atomic optimistic-locking update.

        A single UPDATE whose WHERE clause enforces id, owner scope,
        ``deleted_at IS NULL`` (soft-deleted records can never be modified or
        revived) and the expected revision — evaluated inside the UPDATE, so
        two racing updates cannot both succeed. Row count 0 → distinguish
        404 (missing) from 409 (stale revision or soft-deleted) and raise
        ``StaleRevisionError`` for the latter.
        """
        stmt = (
            update(model)
            .where(model.id == record_id)
            .values(
                revision=model.revision + 1,
                updated_at=datetime.now(UTC),
                **fields,
            )
        )
        if owner_id is not None:
            stmt = stmt.where(model.owner_id == owner_id)
        if hasattr(model, "deleted_at"):  # processing rows have no soft delete
            stmt = stmt.where(model.deleted_at.is_(None))
        if expected_revision is not None:
            stmt = stmt.where(model.revision == expected_revision)
        result = await self.session.execute(stmt)
        if getattr(result, "rowcount", 0) == 0:
            # Distinguish missing (404) from stale revision / deleted (409).
            probe = select(model).where(model.id == record_id)
            if owner_id is not None:
                probe = probe.where(model.owner_id == owner_id)
            rec = (await self.session.execute(probe)).scalar_one_or_none()
            if rec is None:
                return None  # genuinely missing → 404 upstream
            raise StaleRevisionError(model.__name__, record_id, rec.revision)
        # Re-fetch the updated record so callers get fresh values.
        refetch = select(model).where(model.id == record_id)
        record = (await self.session.execute(refetch)).scalar_one_or_none()
        if isinstance(record, (m.Task, m.Project)):
            await append_entity_change(self.session, record)
        return record


class StaleRevisionError(Exception):
    """Raised when an update targets a stale revision (HTTP 409 upstream)."""

    def __init__(self, entity: str, entity_id: str, current_revision: int) -> None:
        self.entity = entity
        self.entity_id = entity_id
        self.current_revision = current_revision
        super().__init__(
            f"{entity} {entity_id} is at revision {current_revision} "
            "(expected revision did not match, or the record is soft-deleted)"
        )


class SqlUserRepository(_Repo):
    async def get_or_create_by_sub(self, sub: str, email: str | None = None) -> m.User:
        stmt = select(m.User).where(m.User.cognito_sub == sub)
        user = (await self.session.execute(stmt)).scalar_one_or_none()
        if user is not None:
            return user
        resolved_email = email or f"{sub}@unknown.local"
        try:
            async with self.session.begin_nested():
                user = m.User(cognito_sub=sub, email=resolved_email)
                self.session.add(user)
                await self.session.flush()
                return user
        except IntegrityError as exc:
            # If a concurrent transaction won the insert race for this cognito_sub,
            # the savepoint was rolled back, keeping the outer session/transaction clean.
            # Query again to return the canonical persisted user row.
            stmt = select(m.User).where(m.User.cognito_sub == sub)
            user = (await self.session.execute(stmt)).scalar_one_or_none()
            if user is not None:
                return user
            # If the user still cannot be found by cognito_sub, the IntegrityError
            # was caused by another constraint (e.g. duplicate email on a different user)
            # and must not be swallowed.
            raise exc

    async def get(self, user_id: str) -> m.User | None:
        stmt = select(m.User).where(m.User.id == user_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_email(self, email: str) -> m.User | None:
        stmt = select(m.User).where(m.User.email == email)
        return (await self.session.execute(stmt)).scalar_one_or_none()


class SqlProjectRepository(_Repo):
    async def create(
        self,
        owner_id: str,
        name: str,
        *,
        description: str | None = None,
        status: str = "active",
        markdown_path: str | None = None,
        metadata: dict | None = None,
    ) -> m.Project:
        proj = m.Project(
            owner_id=owner_id,
            name=name,
            description=description,
            status=status,
            markdown_path=markdown_path,
            metadata_json=metadata,
        )
        self.session.add(proj)
        await self.session.flush()
        await append_entity_change(self.session, proj)
        return proj

    async def get(self, project_id: str, owner_id: str | None = None) -> m.Project | None:
        stmt = select(m.Project).where(m.Project.id == project_id)
        if owner_id is not None:
            stmt = stmt.where(m.Project.owner_id == owner_id)
        stmt = stmt.where(m.Project.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_name(self, owner_id: str, name: str) -> m.Project | None:
        stmt = select(m.Project).where(
            m.Project.owner_id == owner_id,
            m.Project.name == name,
            m.Project.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_owner(
        self,
        owner_id: str,
        *,
        status: str | None = None,
        include_deleted: bool = False,
        limit: int | None = 100,
        offset: int = 0,
    ) -> Sequence[m.Project]:
        stmt = select(m.Project).where(m.Project.owner_id == owner_id)
        if not include_deleted:
            stmt = stmt.where(m.Project.deleted_at.is_(None))
        if status is not None:
            stmt = stmt.where(m.Project.status == status)
        stmt = stmt.order_by(m.Project.created_at.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        if offset:
            stmt = stmt.offset(offset)
        return (await self.session.execute(stmt)).scalars().all()

    async def count_for_owner(
        self,
        owner_id: str,
        *,
        status: str | None = None,
        include_deleted: bool = False,
    ) -> int:
        stmt = select(func.count()).select_from(m.Project).where(m.Project.owner_id == owner_id)
        if not include_deleted:
            stmt = stmt.where(m.Project.deleted_at.is_(None))
        if status is not None:
            stmt = stmt.where(m.Project.status == status)
        return await self.session.scalar(stmt) or 0

    async def update(
        self,
        project_id: str,
        owner_id: str,
        *,
        expected_revision: int | None = None,
        **fields: Any,
    ) -> m.Project | None:
        allowed = {"name", "description", "status", "markdown_path", "metadata_json"}
        clean = {k: v for k, v in fields.items() if k in allowed}
        return await self._guarded_update(m.Project, project_id, owner_id, expected_revision, clean)

    async def soft_delete(
        self,
        project_id: str,
        owner_id: str,
        expected_revision: int | None = None,
    ) -> bool:
        now = datetime.now(UTC)
        updated = await self._guarded_update(
            m.Project,
            project_id,
            owner_id,
            expected_revision,
            {"deleted_at": now, "status": "deleted"},
        )
        return updated is not None


class SqlTaskRepository(_Repo):
    async def create(
        self,
        owner_id: str,
        title: str,
        *,
        project_id: str | None = None,
        capture_id: str | None = None,
        description: str | None = None,
        status: str = "open",
        priority: int | None = None,
        due_date: datetime | None = None,
        hard_deadline: datetime | None = None,
        extraction_meta: dict | None = None,
    ) -> m.Task:
        task = m.Task(
            owner_id=owner_id,
            title=title,
            project_id=project_id,
            capture_id=capture_id,
            description=description,
            status=status,
            priority=priority,
            due_date=due_date,
            hard_deadline=hard_deadline,
            extraction_meta=extraction_meta,
        )
        self.session.add(task)
        await self.session.flush()
        await append_entity_change(self.session, task)
        return task

    async def get(self, task_id: str, owner_id: str | None = None) -> m.Task | None:
        stmt = select(m.Task).where(m.Task.id == task_id)
        if owner_id is not None:
            stmt = stmt.where(m.Task.owner_id == owner_id)
        stmt = stmt.where(m.Task.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_many(self, task_ids: Sequence[str]) -> Sequence[m.Task]:
        if not task_ids:
            return []
        stmt = select(m.Task).where(m.Task.id.in_(list(task_ids)), m.Task.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalars().all()

    async def list_for_owner(
        self,
        owner_id: str,
        *,
        project_id: str | None = None,
        status: str | None = None,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[m.Task]:
        stmt = select(m.Task).where(m.Task.owner_id == owner_id)
        if not include_deleted:
            stmt = stmt.where(m.Task.deleted_at.is_(None))
        if project_id is not None:
            stmt = stmt.where(m.Task.project_id == project_id)
        if status is not None:
            stmt = stmt.where(m.Task.status == status)
        stmt = stmt.order_by(m.Task.created_at.desc()).limit(limit).offset(offset)
        return (await self.session.execute(stmt)).scalars().all()

    async def count_for_owner(
        self,
        owner_id: str,
        *,
        project_id: str | None = None,
        status: str | None = None,
        include_deleted: bool = False,
    ) -> int:
        stmt = select(func.count()).select_from(m.Task).where(m.Task.owner_id == owner_id)
        if not include_deleted:
            stmt = stmt.where(m.Task.deleted_at.is_(None))
        if project_id is not None:
            stmt = stmt.where(m.Task.project_id == project_id)
        if status is not None:
            stmt = stmt.where(m.Task.status == status)
        return await self.session.scalar(stmt) or 0

    async def update(
        self,
        task_id: str,
        owner_id: str,
        *,
        expected_revision: int | None = None,
        **fields: Any,
    ) -> m.Task | None:
        allowed = {
            "title",
            "description",
            "status",
            "priority",
            "due_date",
            "hard_deadline",
            "project_id",
            "extraction_meta",
        }
        clean = {k: v for k, v in fields.items() if k in allowed}
        return await self._guarded_update(m.Task, task_id, owner_id, expected_revision, clean)

    async def soft_delete(
        self,
        task_id: str,
        owner_id: str,
        expected_revision: int | None = None,
    ) -> bool:
        now = datetime.now(UTC)
        updated = await self._guarded_update(
            m.Task,
            task_id,
            owner_id,
            expected_revision,
            {"deleted_at": now, "status": "deleted"},
        )
        return updated is not None


class SqlCaptureRepository(_Repo):
    async def create(
        self,
        owner_id: str,
        content: str,
        *,
        source: str = "web",
        content_type: str = "text",
        project_id: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict | None = None,
    ) -> m.Capture:
        capture = m.Capture(
            owner_id=owner_id,
            content=content,
            source=source,
            content_type=content_type,
            project_id=project_id,
            idempotency_key=idempotency_key,
            metadata_json=metadata,
        )
        self.session.add(capture)
        await self.session.flush()
        return capture

    async def get(self, capture_id: str, owner_id: str | None = None) -> m.Capture | None:
        stmt = select(m.Capture).where(m.Capture.id == capture_id)
        if owner_id is not None:
            stmt = stmt.where(m.Capture.owner_id == owner_id)
        stmt = stmt.where(m.Capture.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def find_by_idempotency_key(self, owner_id: str, key: str) -> m.Capture | None:
        stmt = select(m.Capture).where(
            m.Capture.owner_id == owner_id,
            m.Capture.idempotency_key == key,
            m.Capture.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_owner(
        self,
        owner_id: str,
        *,
        status: str | None = None,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[m.Capture]:
        stmt = select(m.Capture).where(m.Capture.owner_id == owner_id)
        if not include_deleted:
            stmt = stmt.where(m.Capture.deleted_at.is_(None))
        if status is not None:
            stmt = stmt.where(m.Capture.status == status)
        stmt = stmt.order_by(m.Capture.created_at.desc()).limit(limit).offset(offset)
        return (await self.session.execute(stmt)).scalars().all()

    async def update(
        self,
        capture_id: str,
        owner_id: str,
        *,
        expected_revision: int | None = None,
        **fields: Any,
    ) -> m.Capture | None:
        allowed = {"status", "project_id", "metadata_json"}
        clean = {k: v for k, v in fields.items() if k in allowed}
        return await self._guarded_update(m.Capture, capture_id, owner_id, expected_revision, clean)

    async def soft_delete(
        self,
        capture_id: str,
        owner_id: str,
        expected_revision: int | None = None,
    ) -> bool:
        now = datetime.now(UTC)
        updated = await self._guarded_update(
            m.Capture,
            capture_id,
            owner_id,
            expected_revision,
            {"deleted_at": now, "status": "deleted"},
        )
        return updated is not None

    async def count_unprocessed(self) -> int:
        """Captures with no processing row yet — post-dispatch-failure recovery set."""
        stmt = (
            select(func.count(m.Capture.id))
            .outerjoin(m.CaptureProcessing, m.CaptureProcessing.capture_id == m.Capture.id)
            .where(
                m.Capture.deleted_at.is_(None),
                m.CaptureProcessing.id.is_(None),
                m.Capture.status != "deleted",
            )
        )
        return (await self.session.execute(stmt)).scalar_one()


class SqlCaptureProcessingRepository(_Repo):
    async def create(
        self,
        capture_id: str,
        *,
        status: str = "pending",
        max_attempts: int = 3,
    ) -> m.CaptureProcessing:
        rec = m.CaptureProcessing(capture_id=capture_id, status=status, max_attempts=max_attempts)
        self.session.add(rec)
        await self.session.flush()
        return rec

    async def get_by_capture(
        self, capture_id: str, *, for_update: bool = False
    ) -> m.CaptureProcessing | None:
        stmt = select(m.CaptureProcessing).where(m.CaptureProcessing.capture_id == capture_id)
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def update(
        self,
        processing_id: str,
        *,
        expected_revision: int | None = None,
        **fields: Any,
    ) -> m.CaptureProcessing | None:
        allowed = {"status", "attempt", "error", "started_at", "finished_at", "result", "task_ids"}
        clean = {k: v for k, v in fields.items() if k in allowed}
        return await self._guarded_update(
            m.CaptureProcessing, processing_id, None, expected_revision, clean
        )

    async def list_unfinished(self, *, limit: int = 100) -> Sequence[m.CaptureProcessing]:
        """Non-terminal processing rows — the recovery scan set.

        Includes:
          * ``pending`` — persisted but never dispatched (dispatch failure).
          * ``processing`` — worker died mid-attempt (crash recovery).
          * ``failed`` — retryable failure whose message may have been lost
            (e.g. queue outage during requeue); the sweep re-enqueues so
            retries survive restarts. Exhausted retries short-circuit to
            ``dead`` on redelivery (attempt >= max_attempts).

        Terminal rows (succeeded / dead) are excluded — re-enqueueing them
        would burn messages as idempotent no-ops.
        """
        stmt = (
            select(m.CaptureProcessing)
            .where(m.CaptureProcessing.status.in_(("pending", "processing", "failed")))
            .order_by(m.CaptureProcessing.created_at.asc())
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()


class SqlDeviceRepository(_Repo):
    """Device registry persistence implementation."""

    async def register(
        self,
        owner_id: str,
        device_id: str,
        name: str,
        *,
        platform: str = "linux",
        capabilities: dict | None = None,
        public_key: str | None = None,
    ) -> m.Device:
        existing = await self.get_by_device_id(device_id)
        if existing is not None:
            if existing.owner_id != owner_id:
                raise ValueError("Device already registered to another user")
            existing.name = name
            existing.platform = platform
            if capabilities is not None:
                existing.capabilities = capabilities
            if public_key is not None:
                existing.public_key = public_key
            existing.revoked_at = None
            existing.status = "online"
            now = datetime.now(UTC)
            existing.last_seen_at = now
            existing.updated_at = now
            await self.session.flush()
            return existing

        now = datetime.now(UTC)
        device = m.Device(
            id=new_ulid(),
            owner_id=owner_id,
            device_id=device_id,
            name=name,
            platform=platform,
            capabilities=capabilities or {},
            public_key=public_key,
            status="online",
            last_seen_at=now,
            registered_at=now,
            created_at=now,
            updated_at=now,
        )
        self.session.add(device)
        await self.session.flush()
        return device

    async def get_by_device_id(
        self, device_id: str, owner_id: str | None = None
    ) -> m.Device | None:
        stmt = select(m.Device).where(m.Device.device_id == device_id)
        if owner_id is not None:
            stmt = stmt.where(m.Device.owner_id == owner_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, id: str, owner_id: str | None = None) -> m.Device | None:
        stmt = select(m.Device).where(m.Device.id == id)
        if owner_id is not None:
            stmt = stmt.where(m.Device.owner_id == owner_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_owner(
        self,
        owner_id: str,
        *,
        status: str | None = None,
        include_revoked: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[m.Device]:
        stmt = (
            select(m.Device)
            .where(m.Device.owner_id == owner_id)
            .order_by(m.Device.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if not include_revoked:
            stmt = stmt.where(m.Device.revoked_at.is_(None))
        if status:
            stmt = stmt.where(m.Device.status == status)
        return (await self.session.execute(stmt)).scalars().all()

    async def update(
        self,
        device_id: str,
        owner_id: str,
        *,
        expected_revision: int | None = None,
        **fields: Any,
    ) -> m.Device | None:
        allowed = {"name", "platform", "capabilities", "status", "public_key"}
        clean = {k: v for k, v in fields.items() if k in allowed}
        dev = await self.get_by_device_id(device_id, owner_id=owner_id)
        if not dev:
            return None
        return await self._guarded_update(m.Device, dev.id, owner_id, expected_revision, clean)

    async def update_heartbeat(
        self,
        device_id: str,
        owner_id: str,
        *,
        status: str = "online",
        timestamp: datetime | None = None,
    ) -> m.Device | None:
        dev = await self.get_by_device_id(device_id, owner_id=owner_id)
        if not dev or dev.revoked_at is not None:
            return None
        now = timestamp or datetime.now(UTC)
        dev.last_seen_at = now
        dev.status = status
        dev.updated_at = now
        await self.session.flush()
        return dev

    async def revoke(self, device_id: str, owner_id: str) -> bool:
        dev = await self.get_by_device_id(device_id, owner_id=owner_id)
        if not dev or dev.revoked_at is not None:
            return False
        now = datetime.now(UTC)
        dev.revoked_at = now
        dev.status = "offline"
        dev.updated_at = now
        await self.session.flush()
        return True

    async def mark_stale(self, stale_threshold: datetime) -> int:
        stmt = (
            update(m.Device)
            .where(
                m.Device.revoked_at.is_(None),
                m.Device.status.in_(("online", "connecting", "degraded")),
                m.Device.last_seen_at < stale_threshold,
            )
            .values(
                status="stale",
                updated_at=datetime.now(UTC),
            )
        )
        result = await self.session.execute(stmt)
        return getattr(result, "rowcount", 0)


class SqlDeviceOperationRepository(_Repo):
    """SQLAlchemy implementation of DeviceOperationRepository."""

    async def create(
        self,
        owner_id: str,
        device_id: str,
        capability: str,
        params: dict,
        *,
        idempotency_key: str | None = None,
        timeout_seconds: int = 15,
    ) -> m.DeviceOperation:
        now = datetime.now(UTC)
        op = m.DeviceOperation(
            id=new_ulid(),
            owner_id=owner_id,
            device_id=device_id,
            capability=capability,
            params=params,
            status="pending",
            idempotency_key=idempotency_key,
            timeout_seconds=timeout_seconds,
            created_at=now,
            updated_at=now,
            revision=1,
        )
        self.session.add(op)
        await self.session.flush()
        return op

    async def get(self, operation_id: str, owner_id: str | None = None) -> m.DeviceOperation | None:
        stmt = select(m.DeviceOperation).where(m.DeviceOperation.id == operation_id)
        if owner_id:
            stmt = stmt.where(m.DeviceOperation.owner_id == owner_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_idempotency_key(
        self, owner_id: str, device_id: str, idempotency_key: str
    ) -> m.DeviceOperation | None:
        stmt = select(m.DeviceOperation).where(
            m.DeviceOperation.owner_id == owner_id,
            m.DeviceOperation.device_id == device_id,
            m.DeviceOperation.idempotency_key == idempotency_key,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_device(
        self,
        owner_id: str,
        device_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[m.DeviceOperation]:
        stmt = (
            select(m.DeviceOperation)
            .where(
                m.DeviceOperation.owner_id == owner_id,
                m.DeviceOperation.device_id == device_id,
            )
            .order_by(m.DeviceOperation.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if status:
            stmt = stmt.where(m.DeviceOperation.status == status)
        return (await self.session.execute(stmt)).scalars().all()

    async def update_status(
        self,
        operation_id: str,
        owner_id: str,
        status: str,
        *,
        result: dict | None = None,
        error: dict | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> m.DeviceOperation | None:
        op = await self.get(operation_id, owner_id=owner_id)
        if not op:
            return None
        op.status = status
        if result is not None:
            op.result = result
        if error is not None:
            op.error = error
        if started_at is not None:
            op.started_at = started_at
        if finished_at is not None:
            op.finished_at = finished_at
        op.updated_at = datetime.now(UTC)
        op.revision += 1
        await self.session.flush()
        return op

    async def claim_pending(
        self,
        owner_id: str,
        device_id: str,
        *,
        operation_id: str | None = None,
    ) -> m.DeviceOperation | None:
        """Atomically claim the next pending operation for a device using FOR UPDATE SKIP LOCKED."""
        stmt = (
            select(m.DeviceOperation)
            .where(
                m.DeviceOperation.owner_id == owner_id,
                m.DeviceOperation.device_id == device_id,
                m.DeviceOperation.status == "pending",
            )
            .order_by(m.DeviceOperation.created_at.asc())
            .limit(1)
        )
        if operation_id:
            stmt = stmt.where(m.DeviceOperation.id == operation_id)

        bind = self.session.bind
        if bind and bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)

        res = await self.session.execute(stmt)
        op = res.scalar_one_or_none()
        if not op:
            return None

        now = datetime.now(UTC)
        op.status = "running"
        op.started_at = now
        op.updated_at = now
        op.revision += 1
        await self.session.flush()
        return op

    async def list_pending_for_devices(
        self,
        device_ids: Sequence[str],
        *,
        older_than: datetime | None = None,
    ) -> Sequence[m.DeviceOperation]:
        """List pending operations for a set of connected devices (for reconciliation polling)."""
        if not device_ids:
            return []
        stmt = (
            select(m.DeviceOperation)
            .where(
                m.DeviceOperation.device_id.in_(device_ids),
                m.DeviceOperation.status == "pending",
            )
            .order_by(m.DeviceOperation.created_at.asc())
        )
        if older_than is not None:
            stmt = stmt.where(m.DeviceOperation.created_at <= older_than)
        return (await self.session.execute(stmt)).scalars().all()

    async def reap_stale_running(
        self,
        *,
        grace_seconds: int = 10,
    ) -> Sequence[m.DeviceOperation]:
        """Sweep operations stuck in 'running' beyond timeout and mark them outcome_unknown."""
        now = datetime.now(UTC)
        stmt = select(m.DeviceOperation).where(m.DeviceOperation.status == "running")
        bind = self.session.bind
        if bind and bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)

        running_ops = (await self.session.execute(stmt)).scalars().all()
        reaped: list[m.DeviceOperation] = []

        for op in running_ops:
            started = op.started_at or op.created_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            timeout = (op.timeout_seconds or 15) + grace_seconds
            if (now - started).total_seconds() > timeout:
                op.status = "outcome_unknown"
                op.error = {
                    "code": -32000,
                    "message": "Operation timed out or worker crashed while in-flight",
                    "data": {
                        "idempotent": IDEMPOTENT_CAPABILITIES.get(op.capability, False),
                        "capability": op.capability,
                    },
                }
                op.finished_at = now
                op.updated_at = now
                op.revision += 1
                reaped.append(op)

        if reaped:
            await self.session.flush()
        return reaped

    async def reset_for_retry(
        self,
        operation_id: str,
        owner_id: str,
    ) -> m.DeviceOperation | None:
        """Reset a failed, timed out, or outcome_unknown operation to pending for retry."""
        op = await self.get(operation_id, owner_id=owner_id)
        if not op:
            return None
        now = datetime.now(UTC)
        op.status = "pending"
        op.result = None
        op.error = None
        op.started_at = None
        op.finished_at = None
        op.updated_at = now
        op.revision += 1
        await self.session.flush()
        return op


__all__ = [
    "SqlUserRepository",
    "SqlProjectRepository",
    "SqlTaskRepository",
    "SqlCaptureRepository",
    "SqlCaptureProcessingRepository",
    "SqlDeviceRepository",
    "SqlDeviceOperationRepository",
    "StaleRevisionError",
    "new_ulid",
]
