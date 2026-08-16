"""Canonical extraction domain models.

This module defines the stable, provider-agnostic data structures that form the
contract between the SemanticExtractor (probabilistic) and the deterministic
pipeline stages (Validator, Resolver, KnowledgeApplier).

No runtime dependencies (VaultManager, BackgroundProcessor, LLM clients) are
imported here. All models are frozen dataclasses for immutability and
thread-safety.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional, Any


class ExtractionType(Enum):
    """Classification of the extracted knowledge item."""
    TASK = "task"                         # Committed future work ("I need to...", "I must...")
    POSSIBLE_TASK = "possible_task"       # Weak commitment ("I should probably...", "Maybe I'll...")
    PROGRESS = "progress"                 # Explicit progress assertion on existing work
    ACTIVITY = "activity"                 # Logged work without explicit progress claim
    DECISION = "decision"                 # Made choice ("We decided...", "Going with...")
    COMPLETION = "completion"             # Explicitly finished something ("Done with...", "Finished...")
    LEARNING = "learning"                 # Studied or learned something
    PROBLEM = "problem"                   # Identified issue without resolution
    DEADLINE_CONTEXT = "deadline_context" # Temporal/contextual mention ("review Thursday") — NoOp by default
    OBSERVATION = "observation"           # General note or insight
    JOURNAL = "journal"                   # Pure capture — no vault action beyond daily stream


class DeadlineKind(Enum):
    """How a deadline is expressed in the source text (pre-normalization)."""
    EXPLICIT_DATE = "explicit_date"        # "2026-07-30"
    RELATIVE_DAY = "relative_day"          # "today", "tomorrow", "EOD"
    RELATIVE_WEEKDAY = "relative_weekday"  # "next Monday", "this Friday"
    CONTEXTUAL = "contextual"              # "after class", "before lab"
    PERIOD = "period"                      # "this weekend", "next week"
    NONE = "none"                          # No deadline expressed


class ProjectResolution(Enum):
    """Result of resolving a project reference against the vault."""
    EXISTING = "existing"       # Exact match to a vault project
    ALIAS = "alias"             # Matched via configured alias map
    FUZZY = "fuzzy"             # Fuzzy/substring match above threshold
    NEW = "new"                 # No match; confidence high enough to create
    AMBIGUOUS = "ambiguous"     # Multiple candidates; needs clarification
    UNKNOWN = "unknown"         # No match; confidence too low → Inbox fallback


class DeadlineResolution(Enum):
    """Result of normalizing a RawDeadline to a concrete date."""
    RESOLVED = "resolved"       # Normalized to ISO date (YYYY-MM-DD)
    DEFERRED = "deferred"       # Contextual ("after class"); store raw, resolve later
    NONE = "none"               # No deadline expressed or parseable


@dataclass(frozen=True, slots=True)
class RawDeadline:
    """A deadline as expressed in the capture text — unnormalized, provider-agnostic.

    Attributes:
        kind: Classification of the deadline expression.
        raw_text: Exact substring from the capture (e.g., "EOD", "next Monday").
        confidence: SemanticExtractor's confidence in this deadline (0.0–1.0).
    """
    kind: DeadlineKind
    raw_text: str
    confidence: float


@dataclass(frozen=True, slots=True)
class RawProjectRef:
    """A project reference as expressed in the capture text — unresolved.

    Attributes:
        name: The project name/alias as it appears in text (e.g., "Vortex", "maestro").
        confidence: SemanticExtractor's confidence this is a project reference (0.0–1.0).
        is_implicit: True if the reference is indirect ("the project", "it") and
            requires context to resolve; False for explicit names.
    """
    name: str
    confidence: float
    is_implicit: bool = False


@dataclass(frozen=True, slots=True)
class RawExtraction:
    """A single semantic extraction from a capture — pure meaning, zero policy.

    This is the canonical output of the SemanticExtractor. It contains no
    deterministic interpretation (no project creation flags, no Inbox fallback,
    no date normalization). All such decisions belong to the Resolver stage.

    Attributes:
        type: What kind of knowledge this extraction represents.
        description: Actionable, specific description of the extracted item.
        project_ref: Project reference if mentioned, otherwise None.
        deadline: Deadline if explicitly stated or derivable; otherwise None.
            Not every temporal mention is a deadline — use temporal_context for those.
        confidence: The MODEL's confidence it understood the extraction correctly (0.0–1.0).
            Distinct from certainty — do not conflate.
        certainty: The USER's expressed commitment/certainty in the original capture (0.0–1.0).
            "I think the pipeline might work" → certainty≈0.3.
            "I need to finish this today" → certainty≈0.95.
            The model may be highly confident about a low-certainty statement.
        source_excerpt: The specific text span from the capture that led to this item.
            Preserves traceability to the original words.
        temporal_context: The user's temporal expression preserved literally.
            Examples: "tomorrow", "Thursday", "next week", "this evening".
            Not normalized to a date here — that happens in the Resolver using ExtractionContext.now.
            Not every temporal_context becomes a deadline.
        metadata: Provider-specific hints (model name, tokens, latency, etc.).
            Never used for pipeline logic; for observability and migration only.
    """
    type: ExtractionType
    description: str
    project_ref: Optional[RawProjectRef] = None
    deadline: Optional[RawDeadline] = None
    confidence: float = 1.0
    certainty: float = 1.0
    source_excerpt: Optional[str] = None
    temporal_context: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None

    def __post_init__(self):
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})


@dataclass(frozen=True, slots=True)
class ResolvedExtraction:
    """A fully resolved extraction ready for KnowledgeApplier.

    Produced by the Resolver stage. All deterministic decisions (project
    existence, deadline date, ambiguity handling) have been applied. The
    KnowledgeApplier performs only vault writes based on this data.

    Attributes:
        type: Extraction type (unchanged from RawExtraction).
        description: Description (unchanged from RawExtraction).
        project: Exact vault project name, or None for Inbox.
        project_resolution: How the project reference was resolved.
        deadline: Normalized ISO date, or None.
        deadline_resolution: How the deadline was resolved.
        deadline_raw: Original deadline text for audit trail.
        confidence: Minimum confidence across all sub-components.
        metadata: Resolution trace (validation warnings, raw refs, scores).
    """
    type: ExtractionType
    description: str
    project: Optional[str]
    project_resolution: ProjectResolution
    deadline: Optional[date]
    deadline_resolution: DeadlineResolution
    deadline_raw: Optional[str]
    confidence: float
    # Passed through from RawExtraction unchanged — see field docs there.
    certainty: float = 1.0
    source_excerpt: Optional[str] = None
    temporal_context: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None

    def __post_init__(self):
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})