"""Service layer interfaces — the business-logic contract for M1.

Services orchestrate domain logic on top of repositories. These Protocols are
the integration contract for M1-B (capture pipeline), M1-C (API), and M1-D
(deployment); routes and workers will consume them, never repositories
directly.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from personal_os.db.models import Capture, Project, Task

# --------------------------------------------------------------------------
# Shared result types
# --------------------------------------------------------------------------


class ServiceConflictError(Exception):
    """Optimistic-lock failure — maps to HTTP 409 upstream."""

    def __init__(self, entity: str, entity_id: str, current_revision: int) -> None:
        self.entity = entity
        self.entity_id = entity_id
        self.current_revision = current_revision
        super().__init__(
            f"{entity} {entity_id} was modified concurrently (current revision {current_revision})"
        )


class ServiceNotFound(Exception):  # noqa: N818
    """Entity not found (or not owned by caller) — maps to HTTP 404 upstream."""


# --------------------------------------------------------------------------
# Task service
# --------------------------------------------------------------------------


@runtime_checkable
class TaskService(Protocol):
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
    ) -> Task: ...

    async def get_task(self, owner_id: str, task_id: str) -> Task: ...

    async def list_tasks(
        self,
        owner_id: str,
        *,
        project_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Task]: ...

    async def update_task(
        self,
        owner_id: str,
        task_id: str,
        *,
        expected_revision: int | None = None,
        **fields: Any,
    ) -> Task: ...

    async def complete_task(
        self, owner_id: str, task_id: str, *, expected_revision: int | None = None
    ) -> Task: ...

    async def delete_task(
        self, owner_id: str, task_id: str, *, expected_revision: int | None = None
    ) -> None: ...


# --------------------------------------------------------------------------
# Project service
# --------------------------------------------------------------------------


@runtime_checkable
class ProjectService(Protocol):
    async def create_project(
        self,
        owner_id: str,
        name: str,
        *,
        description: str | None = None,
    ) -> Project: ...

    async def get_project(self, owner_id: str, project_id: str) -> Project: ...

    async def list_projects(
        self,
        owner_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Project]: ...

    async def update_project(
        self,
        owner_id: str,
        project_id: str,
        *,
        expected_revision: int | None = None,
        **fields: Any,
    ) -> Project: ...

    async def delete_project(
        self, owner_id: str, project_id: str, *, expected_revision: int | None = None
    ) -> None: ...


# --------------------------------------------------------------------------
# Capture service — the M1-B integration surface
# --------------------------------------------------------------------------


@runtime_checkable
class CaptureService(Protocol):
    async def submit_capture(
        self,
        owner_id: str,
        content: str,
        *,
        source: str = "web",
        content_type: str = "text",
        project_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Capture: ...

    async def get_capture(self, owner_id: str, capture_id: str) -> Capture: ...

    async def list_captures(
        self,
        owner_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Capture]: ...

    async def count_captures(
        self,
        owner_id: str,
        *,
        status: str | None = None,
    ) -> int: ...

    async def delete_capture(self, owner_id: str, capture_id: str) -> None: ...

    async def recover_pending_captures(self, *, limit: int = 100) -> int: ...


# --------------------------------------------------------------------------
# Queue abstraction (SQS in prod; inline/test double locally)
# --------------------------------------------------------------------------


@runtime_checkable
class CaptureQueue(Protocol):
    """At-least-once delivery contract shared by local and SQS queues.

    The database is authoritative. The opaque receipt token must be used to
    acknowledge or defer the specific lease returned by ``dequeue``.
    """

    async def enqueue(self, capture_id: str, owner_id: str) -> str: ...
    async def dequeue(self) -> QueueDelivery | None: ...
    async def ack(self, delivery: QueueDelivery) -> None: ...
    async def retry(self, delivery: QueueDelivery, delay_seconds: int = 0) -> None: ...
    async def extend_visibility(self, delivery: QueueDelivery) -> None: ...


@dataclass(frozen=True, eq=False)
class QueueDelivery:
    """One leased queue delivery; ``ack_token`` is opaque to the worker."""

    capture_id: str
    owner_id: str
    ack_token: str
    receive_count: int
    message_id: str | None = None
    invalid_reason: str | None = None

    def __iter__(self) -> Iterator[str]:
        """Keep tuple unpacking convenient for older local callers."""
        yield self.capture_id
        yield self.owner_id

    def __eq__(self, other: object) -> bool:
        if isinstance(other, QueueDelivery):
            return (
                self.capture_id,
                self.owner_id,
                self.ack_token,
                self.receive_count,
                self.message_id,
                self.invalid_reason,
            ) == (
                other.capture_id,
                other.owner_id,
                other.ack_token,
                other.receive_count,
                other.message_id,
                other.invalid_reason,
            )
        if isinstance(other, tuple):
            return (self.capture_id, self.owner_id) == other
        return NotImplemented


class QueueDispatchError(Exception):
    """Queue dispatch failed after DB persistence. Capture remains durable
    (the caller may commit; the recovery sweep re-enqueues pending work).

    Attributes:
        capture_id: ULID of the capture that was persisted — lets the caller
            (API route) return the real capture ID instead of a placeholder.
    """

    def __init__(self, message: str, capture_id: str | None = None) -> None:
        super().__init__(message)
        self.capture_id = capture_id


# --------------------------------------------------------------------------
# Processing service — implemented in M1-B on top of the extraction pipeline
# --------------------------------------------------------------------------


@runtime_checkable
class ProcessingService(Protocol):
    async def process_capture(self, capture_id: str, owner_id: str) -> CaptureProcessingResult: ...


class CaptureProcessingResult:
    """Return type for ProcessingService.process_capture."""

    def __init__(
        self,
        capture_id: str,
        status: str,
        task_ids: list | None = None,
        error: str | None = None,
    ) -> None:
        self.capture_id = capture_id
        self.status = status
        self.task_ids = task_ids or []
        self.error = error


__all__ = [
    "TaskService",
    "ProjectService",
    "CaptureService",
    "CaptureQueue",
    "QueueDelivery",
    "QueueDispatchError",
    "ProcessingService",
    "CaptureProcessingResult",
    "ServiceConflictError",
    "ServiceNotFound",
]
