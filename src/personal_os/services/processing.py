"""ProcessingService — runs the capture-processing pipeline over the DB.

M1-B core: takes a persisted capture and drives the existing extraction
pipeline (SemanticExtractor → Validator → ProjectResolver → ActionPlanner)
against it, writing results to the new database layer. The extraction pipeline
itself is reused as-is — this service only adapts its outputs to repository
writes, replacing the old vault-file writes (per ARCHITECTURE decisions: the
DB is the persistence layer; the Markdown vault stays untouched by M1).

Processing is idempotent:
  * A processing row in a terminal state (succeeded/failed with attempt
    >= max_attempts) short-circuits — re-processing a delivered message is a
    no-op, so duplicate queue deliveries never double-write tasks.
  * Task writes are deduplicated: tasks are keyed by (owner_id, title) for the
    same capture, so a crash between task-write and status-update replays
    safely — the retry finds the task already present and skips it.

State machine (explicit transitions only):
    pending → processing → succeeded
                        ↘ failed (attempt < max_attempts — retryable)
                        ↘ dead    (terminal — retries exhausted)

The state machine is enforced by _require_transition(); illegal moves raise.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.core.extraction import (
    CreatePossibleTask,
    CreateTask,
    ExtractionContext,
    MarkComplete,
    NoOp,
    SemanticExtractor,
)
from personal_os.db.models import Capture, CaptureProcessing, Project, Task
from personal_os.db.repositories import SqlCaptureProcessingRepository
from personal_os.db.repositories.sqlalchemy import (
    SqlCaptureRepository,
    SqlProjectRepository,
    SqlTaskRepository,
    SqlUserRepository,
)
from personal_os.services.interfaces import CaptureProcessingResult, ServiceNotFound

log = logging.getLogger(__name__)

TERMINAL = {"succeeded", "dead"}


class ProcessingTransitionError(Exception):
    """Illegal processing-state transition (a programming or race error)."""


def _require_transition(current: str, new: str) -> None:
    allowed = {
        ("pending", "processing"),
        ("pending", "succeeded"),
        ("pending", "failed"),
        ("processing", "succeeded"),
        ("processing", "failed"),
        ("processing", "dead"),
        # Crash recovery: a worker that died mid-attempt leaves the row in
        # 'processing'; a re-delivery or recovery sweep legitimately starts a
        # new attempt from that state.
        ("processing", "processing"),
        ("failed", "processing"),
        ("failed", "succeeded"),
        ("failed", "dead"),
    }
    if (current, new) not in allowed:
        raise ProcessingTransitionError(f"Illegal transition {current!r} -> {new!r}")


class TaskActionSink(Protocol):
    """Where task-creating actions are written. Injectable for tests."""

    async def create_task(self, owner_id: str, title: str, **kw: Any) -> Task: ...


class ProcessingServiceImpl:
    """Orchestrates pipeline → repository writes for one capture.

    Args:
        session: DB session (unit of work per processing attempt).
        extractor: SemanticExtractor (LLM injected). Reused unchanged from core.
        task_sink: Optional alternate sink for task writes (failure-injection
            tests). Defaults to a SqlTaskRepository-backed sink.
    """

    def __init__(
        self,
        session: AsyncSession,
        extractor: SemanticExtractor,
        task_sink: TaskActionSink | None = None,
    ) -> None:
        self.session = session
        self.extractor = extractor
        self.users = SqlUserRepository(session)
        self.projects = SqlProjectRepository(session)
        self.tasks = SqlTaskRepository(session)
        self.captures = SqlCaptureRepository(session)
        self.processing = SqlCaptureProcessingRepository(session)
        self.task_sink = task_sink

    # ---- public API ---------------------------------------------------------

    async def process_capture(self, capture_id: str, owner_id: str) -> CaptureProcessingResult:
        """Run the full pipeline for one capture.

        Idempotency contract:
          * Duplicate delivery of an already-terminal job → no-op returning
            the recorded result (no new tasks, no new projects).
          * Crash mid-pipeline (after task writes, before status update) →
            retry finds existing tasks by (owner, title, capture) and skips,
            so retries never duplicate tasks.
        """
        capture = await self.captures.get(capture_id, owner_id)
        if capture is None:
            raise ServiceNotFound("capture", capture_id)
        # Serialize duplicate SQS deliveries for the same capture. The row lock
        # is held through task writes and the caller's commit; later deliveries
        # then observe the terminal/updated state rather than racing to create
        # the same tasks.
        job = await self.processing.get_by_capture(capture_id, for_update=True)
        if job is None:
            # Recovery path: capture persisted but processing row missing
            # (commit/dispatch boundary failure). Create one now.
            job = await self.processing.create(capture_id)

        if job.status in TERMINAL:
            log.info("Capture %s already terminal (%s) — idempotent no-op", capture_id, job.status)
            return CaptureProcessingResult(
                capture_id=capture_id,
                status=job.status,
                task_ids=job.task_ids or [],
                error=job.error,
            )

        if job.attempt >= job.max_attempts:
            # Retries exhausted — mark dead (terminal) so recovery stops.
            await self._transition(job, "dead", error=job.error or "max attempts reached")
            return CaptureProcessingResult(
                capture_id=capture_id, status="dead", task_ids=job.task_ids or [], error=job.error
            )

        # ---- begin attempt ------------------------------------------------
        await self._transition(job, "processing", attempt=job.attempt + 1)

        try:
            task_ids = await self._run_pipeline(capture, owner_id, job=job)
        except Exception as exc:  # noqa: BLE001 — pipeline failure is data, not a crash
            log.warning("Capture %s processing failed: %s", capture_id, exc)
            retryable = job.attempt < job.max_attempts
            new_status = "failed" if retryable else "dead"
            await self._transition(job, new_status, error=str(exc), finished_at=datetime.now(UTC))
            return CaptureProcessingResult(
                capture_id=capture_id,
                status=new_status,
                task_ids=[],
                error=str(exc),
            )

        await self._transition(job, "succeeded", task_ids=task_ids, finished_at=datetime.now(UTC))
        await self.captures.update(capture_id, owner_id, status="processed")
        return CaptureProcessingResult(capture_id=capture_id, status="succeeded", task_ids=task_ids)

    # ---- pipeline ---------------------------------------------------------

    async def _run_pipeline(
        self,
        capture: Capture,
        owner_id: str,
        job: CaptureProcessing | None = None,
    ) -> list[str]:
        """Invoke SemanticExtractor and write task-creating actions.

        Returns the list of task IDs written (deduplicated). Everything the
        pipeline produced is recorded in the processing row's ``result`` JSON,
        including non-task actions, so no information is lost even though only
        tasks/projects are persisted as rows in M1.
        """
        context = await self._build_context(capture, owner_id)
        capture_result = await self.extractor.extract(
            capture.content, context=context, capture_id=capture.id, source=capture.source
        )
        # SemanticExtractor represents provider failures as an empty result
        # plus errors. Treat that as a failed attempt so the worker retries
        # and eventually marks the capture dead, instead of reporting a false
        # success that looks like valid "no extracted actions" output.
        if capture_result.errors and not capture_result.items:
            raise RuntimeError("; ".join(capture_result.errors))

        existing_tasks = await self._existing_task_titles(capture, owner_id)
        seen: set[str] = set()
        task_ids: list[str] = []
        action_log: list[dict[str, object]] = []

        for item in capture_result.items:
            action = item.action
            entry: dict[str, object] = {
                "type": item.raw.type.value,
                "description": item.raw.description,
                "action": type(action).__name__,
            }
            if hasattr(action, "project"):
                entry["project_name"] = action.project
            if isinstance(action, CreateTask) and action.deadline is not None:
                entry["deadline"] = action.deadline.isoformat()
            if isinstance(action, (CreateTask, CreatePossibleTask)):
                title = action.title
                key = title.lower().strip()
                project_id = await self._resolve_project(owner_id, action.project, entry)
                due_date = None
                if isinstance(action, CreateTask) and action.deadline is not None:
                    due_date = datetime.combine(action.deadline, datetime.min.time(), tzinfo=UTC)
                if key in seen:
                    entry["deduplicated"] = True
                else:
                    seen.add(key)
                    if key in existing_tasks:
                        # Crash-replay: preserve the original ID instead of
                        # creating a duplicate after a partial attempt.
                        entry["deduplicated"] = True
                        entry["task_id"] = existing_tasks[key]
                        task_ids.append(existing_tasks[key])
                    else:
                        sink = self.task_sink or self._default_sink()
                        task = await sink.create_task(
                            owner_id,
                            title,
                            capture_id=capture.id,
                            status="open",
                            project_id=project_id,
                            due_date=due_date,
                        )
                        entry["task_id"] = task.id
                        task_ids.append(task.id)
            elif isinstance(action, MarkComplete):
                matched = await self._match_completion(owner_id, action.description, action.project)
                if matched is None:
                    entry["processing"] = "deferred_unmatched_or_ambiguous"
                else:
                    completed_task: Task | None = matched
                    if matched.status != "done":
                        completed_task = await self.tasks.update(
                            matched.id, owner_id, status="done"
                        )
                    entry["processing"] = (
                        "completed" if completed_task else "deferred_unmatched_or_ambiguous"
                    )
                    if completed_task:
                        entry["task_id"] = completed_task.id
                        task_ids.append(completed_task.id)
            elif isinstance(action, NoOp):
                entry["processing"] = "intentional_no_op"
            else:
                # The raw capture and typed action are retained in the audit log.
                entry["processing"] = "deferred_m1_persistence_not_supported"
            action_log.append(entry)

        # Record the full pipeline trace in the processing row (audit + observability).
        deferred_count = sum(
            str(item.get("processing", "")).startswith("deferred_") for item in action_log
        )
        if job is not None:
            job_id = job.id
        else:
            proc = await self.processing.get_by_capture(capture.id)
            if proc is None:
                raise ServiceNotFound("capture_processing", capture.id)
            job_id = proc.id
        await self.processing.update(
            job_id,
            result={
                "items": action_log,
                "errors": list(capture_result.errors),
                "deferred_action_count": deferred_count,
                "source_text_preserved": capture_result.source_text == capture.content,
            },
            error=(
                f"{deferred_count} extraction action(s) explicitly deferred"
                if deferred_count
                else None
            ),
        )
        return task_ids

    def _default_sink(self) -> TaskActionSink:
        parent = self

        class _RepoSink:
            async def create_task(self, owner_id: str, title: str, **kw: Any) -> Task:
                task = await parent.tasks.create(owner_id, title, **kw)
                return task

        return _RepoSink()

    async def _resolve_project(
        self, owner_id: str, name: str | None, entry: dict[str, object]
    ) -> str | None:
        if name is None:
            return None
        projects = await self.projects.list_for_owner(owner_id, limit=None)
        matches = [p for p in projects if p.name.casefold() == name.casefold()]
        if len(matches) == 1:
            entry["project_id"] = matches[0].id
            return matches[0].id
        entry["project_resolution"] = "missing" if not matches else "ambiguous"
        return None

    async def _match_completion(
        self, owner_id: str, description: str, project_name: str | None
    ) -> Task | None:
        rows = await self.session.execute(
            select(Task)
            .outerjoin(Project, Task.project_id == Project.id)
            .where(
                Task.owner_id == owner_id,
                Task.deleted_at.is_(None),
                or_(Task.project_id.is_(None), Project.owner_id == owner_id),
            )
        )
        matches = [
            task
            for task in rows.scalars()
            if task.title.casefold().strip() == description.casefold().strip()
        ]
        if project_name is not None:
            project_rows = await self.projects.list_for_owner(owner_id, limit=None)
            project_ids = {
                project.id
                for project in project_rows
                if project.name.casefold() == project_name.casefold()
            }
            if len(project_ids) != 1:
                return None
            matches = [task for task in matches if task.project_id in project_ids]
        return matches[0] if len(matches) == 1 else None

    async def _transition(
        self,
        job: CaptureProcessing,
        new_status: str,
        *,
        attempt: int | None = None,
        error: str | None = None,
        task_ids: list[str] | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        """Explicit state-machine transition on the processing row."""
        _require_transition(job.status, new_status)
        fields: dict[str, Any] = {"status": new_status}
        if attempt is not None:
            fields["attempt"] = attempt
        if error is not None:
            fields["error"] = error
        if task_ids is not None:
            fields["task_ids"] = task_ids
        if finished_at is not None:
            fields["finished_at"] = finished_at
        updated = await self.processing.update(job.id, **fields)
        if updated is None:
            raise ProcessingTransitionError(
                f"Processing row {job.id} vanished during transition to {new_status}"
            )
        job.status = new_status
        # Mirror mutation fields onto the local object so subsequent logic
        # (e.g. retryability checks in the except path) sees current values.
        if attempt is not None:
            job.attempt = attempt
        if error is not None:
            job.error = error

    async def _existing_task_titles(self, capture: Capture, owner_id: str) -> dict[str, str]:
        """Titles of tasks already linked to this capture — crash-replay guard.

        Returns a mapping of normalized title -> task ID so retries can
        preserve the original task IDs instead of dropping deduplicated rows.
        """
        rows = await self.session.execute(
            select(Task.title, Task.id).where(
                Task.capture_id == capture.id,
                Task.owner_id == owner_id,
                Task.deleted_at.is_(None),
            )
        )
        return {r[0].lower().strip(): r[1] for r in rows}

    async def _build_context(self, capture: Capture, owner_id: str) -> ExtractionContext:
        """Build ExtractionContext from DB state (projects, active tasks)."""
        projects = await self.projects.list_for_owner(owner_id)
        open_tasks = await self.tasks.list_for_owner(owner_id, status="open")
        return ExtractionContext(
            now=datetime.now(UTC),
            timezone="UTC",
            projects=[p.name for p in projects],
            active_tasks=[t.title for t in open_tasks],
            current_project=None,
        )


__all__ = ["ProcessingServiceImpl", "ProcessingTransitionError"]
