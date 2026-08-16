"""Extraction pipeline package."""

from personal_os.core.extraction.models import (
    ExtractionType,
    DeadlineKind,
    ProjectResolution,
    DeadlineResolution,
    RawDeadline,
    RawProjectRef,
    RawExtraction,
    ResolvedExtraction,
)
from personal_os.core.extraction.actions import (
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
    VaultAction,
)
from personal_os.core.extraction.action_planner import ActionPlanner
from personal_os.core.extraction.contexts import ExtractionContext
from personal_os.core.extraction.semantic_extractor import (
    SemanticExtractor,
    CaptureResult,
    ExtractionResult,
)

__all__ = [
    # models
    "ExtractionType",
    "DeadlineKind",
    "ProjectResolution",
    "DeadlineResolution",
    "RawDeadline",
    "RawProjectRef",
    "RawExtraction",
    "ResolvedExtraction",
    # actions
    "CreateTask",
    "CreatePossibleTask",
    "RecordProgress",
    "RecordActivity",
    "RecordProblem",
    "AddDecision",
    "MarkComplete",
    "AppendLearning",
    "AppendNote",
    "NoOp",
    "VaultAction",
    # planner
    "ActionPlanner",
    # pipeline
    "ExtractionContext",
    "SemanticExtractor",
    "CaptureResult",
    "ExtractionResult",
]