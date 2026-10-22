"""Repository interfaces — the persistence contract for M1.

These are the boundaries between the domain/service layer and the database.
The production implementation uses PostgreSQL. Protocol classes keep domain
services independent of SQLAlchemy and allow isolated test doubles.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from personal_os.db.models import (
    Capture,
    CaptureProcessing,
    Device,
    DeviceOperation,
    Project,
    Task,
    User,
)

# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------


@runtime_checkable
class UserRepository(Protocol):
    async def get_or_create_by_sub(self, sub: str, email: str | None = None) -> User: ...
    async def get(self, user_id: str) -> User | None: ...
    async def get_by_email(self, email: str) -> User | None: ...


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------


@runtime_checkable
class ProjectRepository(Protocol):
    async def create(
        self,
        owner_id: str,
        name: str,
        *,
        description: str | None = None,
        status: str = "active",
        markdown_path: str | None = None,
        metadata: dict | None = None,
    ) -> Project: ...

    async def get(self, project_id: str, owner_id: str | None = None) -> Project | None: ...
    async def get_by_name(self, owner_id: str, name: str) -> Project | None: ...

    async def list_for_owner(
        self,
        owner_id: str,
        *,
        status: str | None = None,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Project]: ...

    async def update(
        self,
        project_id: str,
        owner_id: str,
        *,
        expected_revision: int | None = None,
        **fields: Any,
    ) -> Project | None: ...

    async def soft_delete(self, project_id: str, owner_id: str) -> bool: ...


# --------------------------------------------------------------------------
# Tasks
# --------------------------------------------------------------------------


@runtime_checkable
class TaskRepository(Protocol):
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
        extraction_meta: dict | None = None,
    ) -> Task: ...

    async def get(self, task_id: str, owner_id: str | None = None) -> Task | None: ...
    async def get_many(self, task_ids: Sequence[str]) -> Sequence[Task]: ...

    async def list_for_owner(
        self,
        owner_id: str,
        *,
        project_id: str | None = None,
        status: str | None = None,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Task]: ...

    async def update(
        self,
        task_id: str,
        owner_id: str,
        *,
        expected_revision: int | None = None,
        **fields: Any,
    ) -> Task | None: ...

    async def soft_delete(self, task_id: str, owner_id: str) -> bool: ...


# --------------------------------------------------------------------------
# Captures
# --------------------------------------------------------------------------


@runtime_checkable
class CaptureRepository(Protocol):
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
    ) -> Capture: ...

    async def get(self, capture_id: str, owner_id: str | None = None) -> Capture | None: ...

    async def find_by_idempotency_key(self, owner_id: str, key: str) -> Capture | None: ...

    async def count_unprocessed(self) -> int: ...

    async def list_for_owner(
        self,
        owner_id: str,
        *,
        status: str | None = None,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Capture]: ...

    async def update(
        self,
        capture_id: str,
        owner_id: str,
        *,
        expected_revision: int | None = None,
        **fields: Any,
    ) -> Capture | None: ...

    async def soft_delete(self, capture_id: str, owner_id: str) -> bool: ...


# --------------------------------------------------------------------------
# Capture processing jobs
# --------------------------------------------------------------------------


@runtime_checkable
class CaptureProcessingRepository(Protocol):
    async def create(
        self,
        capture_id: str,
        *,
        status: str = "pending",
        max_attempts: int = 3,
    ) -> CaptureProcessing: ...

    async def get_by_capture(self, capture_id: str) -> CaptureProcessing | None: ...

    async def update(
        self,
        processing_id: str,
        *,
        expected_revision: int | None = None,
        **fields: Any,
    ) -> CaptureProcessing | None: ...

    async def list_unfinished(self, *, limit: int = 100) -> Sequence[CaptureProcessing]: ...


# --------------------------------------------------------------------------
# Devices
# --------------------------------------------------------------------------


@runtime_checkable
class DeviceRepository(Protocol):
    async def register(
        self,
        owner_id: str,
        device_id: str,
        name: str,
        *,
        platform: str = "linux",
        capabilities: dict | None = None,
        public_key: str | None = None,
    ) -> Device: ...

    async def get_by_device_id(
        self, device_id: str, owner_id: str | None = None
    ) -> Device | None: ...

    async def get_by_id(self, id: str, owner_id: str | None = None) -> Device | None: ...

    async def list_for_owner(
        self,
        owner_id: str,
        *,
        status: str | None = None,
        include_revoked: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Device]: ...

    async def update(
        self,
        device_id: str,
        owner_id: str,
        *,
        expected_revision: int | None = None,
        **fields: Any,
    ) -> Device | None: ...

    async def update_heartbeat(
        self,
        device_id: str,
        owner_id: str,
        *,
        status: str = "online",
        timestamp: datetime | None = None,
    ) -> Device | None: ...

    async def revoke(self, device_id: str, owner_id: str) -> bool: ...

    async def mark_stale(self, stale_threshold: datetime) -> int: ...


# --------------------------------------------------------------------------
# Device Operations
# --------------------------------------------------------------------------


@runtime_checkable
class DeviceOperationRepository(Protocol):
    async def create(
        self,
        owner_id: str,
        device_id: str,
        capability: str,
        params: dict,
        *,
        idempotency_key: str | None = None,
        timeout_seconds: int = 15,
    ) -> DeviceOperation: ...

    async def get(
        self, operation_id: str, owner_id: str | None = None
    ) -> DeviceOperation | None: ...

    async def get_by_idempotency_key(
        self, owner_id: str, device_id: str, idempotency_key: str
    ) -> DeviceOperation | None: ...

    async def list_for_device(
        self,
        owner_id: str,
        device_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[DeviceOperation]: ...

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
    ) -> DeviceOperation | None: ...


__all__ = [
    "UserRepository",
    "ProjectRepository",
    "TaskRepository",
    "CaptureRepository",
    "CaptureProcessingRepository",
    "DeviceRepository",
    "DeviceOperationRepository",
]
