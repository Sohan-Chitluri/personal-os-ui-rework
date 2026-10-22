"""Concrete M1 services on top of the repository layer.

All business logic lives here — routes (M1-C) and workers (M1-B) call these,
never repositories directly. Sessions are passed in by the caller (API
dependency or worker unit-of-work) so a single request = single transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.db.models import Capture, CaptureDispatchOutbox, Project, Task
from personal_os.db.repositories import (
    SqlCaptureProcessingRepository,
    SqlCaptureRepository,
    SqlProjectRepository,
    SqlTaskRepository,
    SqlUserRepository,
    StaleRevisionError,
)
from personal_os.services.interfaces import (
    CaptureQueue,
    ServiceConflictError,
    ServiceNotFound,
)


class _BaseService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = SqlUserRepository(session)
        self.projects = SqlProjectRepository(session)
        self.tasks = SqlTaskRepository(session)
        self.captures = SqlCaptureRepository(session)
        self.processing = SqlCaptureProcessingRepository(session)

    async def _ensure_user(self, owner_id: str) -> None:
        user = await self.users.get(owner_id)
        if user is None:
            raise ServiceNotFound("user", owner_id)


class TaskServiceImpl(_BaseService):
    async def create_task(
        self,
        owner_id: str,
        title: str,
        *,
        project_id: str | None = None,
        description: str | None = None,
        priority: int | None = None,
        due_date: datetime | None = None,
        hard_deadline: datetime | None = None,
        extraction_meta: dict | None = None,
    ) -> Task:
        await self._ensure_user(owner_id)
        if project_id is not None:
            project = await self.projects.get(project_id, owner_id)
            if project is None:
                raise ServiceNotFound("project", project_id)
        return await self.tasks.create(
            owner_id,
            title,
            project_id=project_id,
            description=description,
            priority=priority,
            due_date=due_date,
            hard_deadline=hard_deadline,
            extraction_meta=extraction_meta,
        )

    async def get_task(self, owner_id: str, task_id: str) -> Task:
        task = await self.tasks.get(task_id, owner_id)
        if task is None:
            raise ServiceNotFound("task", task_id)
        return task

    async def list_tasks(self, owner_id: str, **filters: Any) -> Sequence[Task]:
        await self._ensure_user(owner_id)
        return await self.tasks.list_for_owner(owner_id, **filters)

    async def count_tasks(
        self,
        owner_id: str,
        *,
        project_id: str | None = None,
        status: str | None = None,
    ) -> int:
        await self._ensure_user(owner_id)
        return await self.tasks.count_for_owner(owner_id, project_id=project_id, status=status)

    count_for_owner = count_tasks

    async def update_task(
        self,
        owner_id: str,
        task_id: str,
        *,
        expected_revision: int | None = None,
        **fields: Any,
    ) -> Task:
        await self._ensure_user(owner_id)
        # Tenant isolation: a destination project_id must be owned by the caller.
        # The repository whitelists 'project_id', but ownership must be validated
        # at the service boundary — same rule as create_task.
        if fields.get("project_id") is not None:
            project = await self.projects.get(fields["project_id"], owner_id)
            if project is None:
                raise ServiceNotFound("project", fields["project_id"])
        try:
            task = await self.tasks.update(
                task_id, owner_id, expected_revision=expected_revision, **fields
            )
        except StaleRevisionError:
            # Distinguish 404 from 409
            existing = await self.tasks.get(task_id, owner_id)
            if existing is None:
                raise ServiceNotFound("task", task_id) from None
            raise ServiceConflictError("task", task_id, existing.revision) from None
        if task is None:
            # Distinguish 404 from 409
            existing = await self.tasks.get(task_id, owner_id)
            if existing is None:
                raise ServiceNotFound("task", task_id)
            raise ServiceConflictError("task", task_id, existing.revision)
        return task

    async def complete_task(
        self,
        owner_id: str,
        task_id: str,
        *,
        expected_revision: int | None = None,
    ) -> Task:
        return await self.update_task(
            owner_id, task_id, expected_revision=expected_revision, status="done"
        )

    async def delete_task(
        self,
        owner_id: str,
        task_id: str,
        *,
        expected_revision: int | None = None,
    ) -> None:
        await self._ensure_user(owner_id)
        try:
            deleted = await self.tasks.soft_delete(
                task_id, owner_id, expected_revision=expected_revision
            )
        except StaleRevisionError:
            existing = await self.tasks.get(task_id, owner_id)
            if existing is None:
                raise ServiceNotFound("task", task_id) from None
            raise ServiceConflictError("task", task_id, existing.revision) from None
        if not deleted:
            existing = await self.tasks.get(task_id, owner_id)
            if existing is None:
                raise ServiceNotFound("task", task_id)
            raise ServiceConflictError("task", task_id, existing.revision)


class ProjectServiceImpl(_BaseService):
    async def create_project(
        self,
        owner_id: str,
        name: str,
        *,
        description: str | None = None,
    ) -> Project:
        await self._ensure_user(owner_id)
        existing = await self.projects.get_by_name(owner_id, name)
        if existing is not None:
            raise ServiceConflictError("project", existing.id, existing.revision)
        return await self.projects.create(owner_id, name, description=description)

    async def get_project(self, owner_id: str, project_id: str) -> Project:
        project = await self.projects.get(project_id, owner_id)
        if project is None:
            raise ServiceNotFound("project", project_id)
        return project

    async def list_projects(self, owner_id: str, **filters: Any) -> Sequence[Project]:
        await self._ensure_user(owner_id)
        return await self.projects.list_for_owner(owner_id, **filters)

    async def count_projects(
        self,
        owner_id: str,
        *,
        status: str | None = None,
    ) -> int:
        await self._ensure_user(owner_id)
        return await self.projects.count_for_owner(owner_id, status=status)

    count_for_owner = count_projects

    async def update_project(
        self,
        owner_id: str,
        project_id: str,
        *,
        expected_revision: int | None = None,
        **fields: Any,
    ) -> Project:
        await self._ensure_user(owner_id)
        try:
            project = await self.projects.update(
                project_id, owner_id, expected_revision=expected_revision, **fields
            )
        except StaleRevisionError:
            existing = await self.projects.get(project_id, owner_id)
            if existing is None:
                raise ServiceNotFound("project", project_id) from None
            raise ServiceConflictError("project", project_id, existing.revision) from None
        if project is None:
            existing = await self.projects.get(project_id, owner_id)
            if existing is None:
                raise ServiceNotFound("project", project_id)
            raise ServiceConflictError("project", project_id, existing.revision)
        return project

    async def delete_project(
        self,
        owner_id: str,
        project_id: str,
        *,
        expected_revision: int | None = None,
    ) -> None:
        await self._ensure_user(owner_id)
        try:
            deleted = await self.projects.soft_delete(
                project_id, owner_id, expected_revision=expected_revision
            )
        except StaleRevisionError:
            existing = await self.projects.get(project_id, owner_id)
            if existing is None:
                raise ServiceNotFound("project", project_id) from None
            raise ServiceConflictError("project", project_id, existing.revision) from None
        if not deleted:
            existing = await self.projects.get(project_id, owner_id)
            if existing is None:
                raise ServiceNotFound("project", project_id)
            raise ServiceConflictError("project", project_id, existing.revision)


class CaptureServiceImpl(_BaseService):
    """Persist captures and their dispatch intent in one database transaction.

    A worker publishes the outbox row after the request commits. A crash before
    publication leaves it pending; a crash after publishing may lead to a
    duplicate queue delivery, which the idempotent processing service handles.
    """

    def __init__(self, session: AsyncSession, queue: CaptureQueue | None = None) -> None:
        super().__init__(session)
        self.queue = queue

    async def submit_capture(
        self,
        owner_id: str,
        content: str,
        *,
        source: str = "web",
        content_type: str = "text",
        project_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Capture:
        await self._ensure_user(owner_id)
        if project_id is not None:
            project = await self.projects.get(project_id, owner_id)
            if project is None:
                raise ServiceNotFound("project", project_id)

        if idempotency_key is not None:
            existing = await self.captures.find_by_idempotency_key(owner_id, idempotency_key)
            if existing is not None:
                return existing  # idempotent replay — same capture returned

        capture = await self.captures.create(
            owner_id,
            content,
            source=source,
            content_type=content_type,
            project_id=project_id,
            idempotency_key=idempotency_key,
        )
        # Create the processing job row in the same transaction so a capture
        # never exists without its pipeline record.
        await self.processing.create(capture.id)
        self.session.add(CaptureDispatchOutbox(capture_id=capture.id))
        await self.session.flush()

        return capture

    async def recover_pending_captures(self, *, limit: int = 100) -> int:
        """Ensure legacy unfinished captures have a durable dispatch intent.

        New submissions always create an outbox row transactionally. This
        startup compatibility sweep repairs only unfinished captures missing
        that row; the outbox dispatcher performs the actual queue send.

        Soft-deleted / missing captures: their processing rows are terminalized
        (``dead``) instead of being silently skipped forever — otherwise they
        would re-appear in every sweep and starve recovery of real work.
        """
        if self.queue is None:
            return 0
        unfinished = await self.processing.list_unfinished(limit=limit)
        added = 0
        for job in unfinished:
            capture = await self.captures.get(job.capture_id)
            if capture is None:
                # Soft-deleted (get() excludes them) or orphaned: terminalize
                # so the sweep stops churning on it.
                await self.processing.update(
                    job.id, status="dead", error="capture deleted or missing"
                )
                continue
            stmt = select(CaptureDispatchOutbox).where(
                CaptureDispatchOutbox.capture_id == job.capture_id
            )
            outbox = (await self.session.execute(stmt)).scalar_one_or_none()
            if outbox is None:
                # Recover captures created before the outbox migration or by
                # an interrupted legacy request.
                self.session.add(CaptureDispatchOutbox(capture_id=job.capture_id))
                added += 1
            elif not getattr(self.queue, "durable", False) and outbox.state == "dispatched":
                # A process restart loses the local in-memory queue. Re-arm
                # unfinished rows only for that volatile backend; SQS retains
                # its own leases and must not be flooded with recovery copies.
                outbox.state = "pending"
                outbox.dispatched_at = None
                outbox.next_attempt_at = None
                outbox.last_error = None
                added += 1
        return added

    async def get_capture(self, owner_id: str, capture_id: str) -> Capture:
        capture = await self.captures.get(capture_id, owner_id)
        if capture is None:
            raise ServiceNotFound("capture", capture_id)
        return capture

    async def list_captures(self, owner_id: str, **filters: Any) -> Sequence[Capture]:
        await self._ensure_user(owner_id)
        return await self.captures.list_for_owner(owner_id, **filters)

    async def delete_capture(
        self,
        owner_id: str,
        capture_id: str,
        *,
        expected_revision: int | None = None,
    ) -> None:
        await self._ensure_user(owner_id)
        try:
            deleted = await self.captures.soft_delete(
                capture_id, owner_id, expected_revision=expected_revision
            )
        except StaleRevisionError:
            existing = await self.captures.get(capture_id, owner_id)
            if existing is None:
                raise ServiceNotFound("capture", capture_id) from None
            raise ServiceConflictError("capture", capture_id, existing.revision) from None
        if not deleted:
            existing = await self.captures.get(capture_id, owner_id)
            if existing is None:
                raise ServiceNotFound("capture", capture_id)
            raise ServiceConflictError("capture", capture_id, existing.revision)


__all__ = ["TaskServiceImpl", "ProjectServiceImpl", "CaptureServiceImpl"]
