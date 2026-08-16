"""Typed vault actions — the contract between ActionPlanner and KnowledgeApplier.

Every vault mutation produced by the processing pipeline MUST be expressed as
one of these typed actions. LLM output never reaches the filesystem directly.

Flow:
    SemanticExtractor → RawExtraction → Validator → ProjectResolver
        → ActionPlanner → VaultAction → KnowledgeApplier → Vault

The KnowledgeApplier receives VaultAction instances and applies them
atomically. All policy about which file to write, what format to use, and how
to de-duplicate lives in KnowledgeApplier. ActionPlanner only maps semantic
intent to action type — no vault access, no I/O.

capture_id (present on all action types):
    The ULID of the originating capture. Used to annotate vault writes with a
    traceability link back to the Daily Stream entry. Empty string = no
    traceability (e.g. legacy/regex path).

Actions with project=None and no Inbox fallback:
    RecordActivity, RecordProblem — when project is None, KnowledgeApplier
    treats these as NoOp. The raw capture is already in the Daily Stream.
    Do not manufacture a fake project.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Optional, Union


@dataclass(frozen=True, slots=True)
class CreateTask:
    """Create a new committed task item in the vault.

    If ``project`` is None the task goes to 00-Inbox/Tasks.md.
    Committed tasks come from captures with high user certainty.
    """
    title: str
    project: Optional[str]
    deadline: Optional[date] = None
    source: str = "Text"
    capture_id: str = ""


@dataclass(frozen=True, slots=True)
class CreatePossibleTask:
    """Create a possible/speculative task — weak commitment, low certainty.

    Goes to the '## Possible Tasks' section of Tasks.md (created if absent).
    NOT an active daily task. Must be explicitly promoted by the user.

    Examples: "I should probably...", "Maybe I'll...", "I might..."
    """
    title: str
    project: Optional[str]          # None → 00-Inbox/Tasks.md ## Possible Tasks
    source: str = "Text"
    capture_id: str = ""


@dataclass(frozen=True, slots=True)
class RecordProgress:
    """Append a progress entry to a project's Progress.md."""
    description: str
    project: str
    hours: Optional[float] = None
    source: str = "Text"
    capture_id: str = ""


@dataclass(frozen=True, slots=True)
class RecordActivity:
    """Log a work session without asserting explicit progress.

    Represents "I've been working on X" without claiming X is done or partially done.
    If project is None → NoOp (the Daily Stream already records this).
    """
    description: str
    project: Optional[str] = None   # None → NoOp; do NOT invent a fake project
    source: str = "Text"
    capture_id: str = ""


@dataclass(frozen=True, slots=True)
class RecordProblem:
    """Note an identified problem or issue to Notes.md.

    If project is None → NoOp (the Daily Stream already records this).
    Stored with a [Problem] marker and #problem tag for later retrieval.
    """
    description: str
    project: Optional[str] = None   # None → NoOp
    source: str = "Text"
    capture_id: str = ""


@dataclass(frozen=True, slots=True)
class AddDecision:
    """Append a decision to a project's Decisions.md (or Inbox if no project)."""
    decision: str
    project: Optional[str]
    source: str = "Text"
    capture_id: str = ""


@dataclass(frozen=True, slots=True)
class MarkComplete:
    """Mark an existing task complete via fuzzy description match.

    KnowledgeApplier scans the project's Tasks.md for a '- [ ]' line whose
    text best matches ``description`` and flips it to '- [x]'.
    """
    description: str
    project: Optional[str]
    source: str = "Text"
    capture_id: str = ""


@dataclass(frozen=True, slots=True)
class AppendLearning:
    """Append a learning/study session entry to 03-Skills/<skill_area>/Progress.md."""
    topic: str
    skill_area: str  # VLSI | Embedded | Linux | Software | Mathematics | General
    source: str = "Text"
    capture_id: str = ""


@dataclass(frozen=True, slots=True)
class AppendNote:
    """Append a note or observation to a project's Notes.md.

    If project is None → NoOp (the Daily Stream already records this).
    """
    content: str
    project: Optional[str]  # None → NoOp (not forced to Inbox)
    source: str = "Text"
    capture_id: str = ""


@dataclass(frozen=True, slots=True)
class NoOp:
    """No additional vault action required.

    The raw capture is already persisted in the Daily Stream. This action is
    used when:
    - The item is a pure journal/capture entry
    - Activity/problem/note with no resolved project (Daily Stream is sufficient)
    - DEADLINE_CONTEXT — temporal mention that is not a committed task deadline
    """
    reason: str


# The VaultAction union — KnowledgeApplier dispatches on the concrete type.
VaultAction = Union[
    CreateTask,
    CreatePossibleTask,
    RecordProgress,
    RecordActivity,
    RecordProblem,
    AddDecision,
    MarkComplete,
    AppendLearning,
    AppendNote,
    NoOp,
]
