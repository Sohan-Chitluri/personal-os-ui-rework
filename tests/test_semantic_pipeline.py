"""Tests for the Wave 2 semantic extraction pipeline.

Tests A–F cover the full pipeline from SemanticExtractor through ActionPlanner,
with typed VaultAction assertions and vault-integration spot checks.

LLMProvider is mocked in all async tests — no network calls required.
"""
from __future__ import annotations

import pytest
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

from personal_os.core.extraction import (
    ActionPlanner,
    CaptureResult,
    CreatePossibleTask,
    CreateTask,
    ExtractionContext,
    ExtractionResult,
    ExtractionType,
    MarkComplete,
    NoOp,
    RecordActivity,
    RecordProblem,
    RecordProgress,
    SemanticExtractor,
)
from personal_os.core.extraction.action_planner import (
    PROGRESS_CERTAINTY_THRESHOLD,
    TASK_CERTAINTY_THRESHOLD,
)
from personal_os.core.extraction.models import (
    DeadlineKind,
    DeadlineResolution,
    ProjectResolution,
    RawDeadline,
    RawExtraction,
    RawProjectRef,
    ResolvedExtraction,
)
from personal_os.core.llm import LLMProvider


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_mock_provider(raw_extractions: list[RawExtraction]) -> LLMProvider:
    provider = MagicMock(spec=LLMProvider)
    provider.extract = AsyncMock(return_value=raw_extractions)
    provider.close = AsyncMock()
    return provider


def make_raw(
    type_: ExtractionType,
    description: str,
    project: str | None = None,
    certainty: float = 0.9,
    temporal_context: str | None = None,
    source_excerpt: str | None = None,
    confidence: float = 0.85,
) -> RawExtraction:
    return RawExtraction(
        type=type_,
        description=description,
        project_ref=RawProjectRef(name=project, confidence=confidence, is_implicit=False)
        if project
        else None,
        deadline=None,
        confidence=confidence,
        certainty=certainty,
        source_excerpt=source_excerpt,
        temporal_context=temporal_context,
        metadata={},
    )


def make_resolved(
    type_: ExtractionType,
    description: str,
    project: str | None = None,
    certainty: float = 0.9,
    deadline: date | None = None,
) -> ResolvedExtraction:
    return ResolvedExtraction(
        type=type_,
        description=description,
        project=project,
        project_resolution=ProjectResolution.EXISTING if project else ProjectResolution.UNKNOWN,
        deadline=deadline,
        deadline_resolution=DeadlineResolution.RESOLVED if deadline else DeadlineResolution.NONE,
        deadline_raw=None,
        confidence=0.85,
        certainty=certainty,
        source_excerpt=None,
        temporal_context=None,
        metadata={},
    )


_CORDIC_INPUT = (
    "I've been working on the CORDIC pipeline most of the evening. "
    "The fixed-point stage is mostly working now, but the scaling is still giving me weird results. "
    "I think I need to revisit that tomorrow because the review is on Thursday."
)

_CORDIC_MOCK = [
    make_raw(ExtractionType.ACTIVITY, "Working on CORDIC pipeline", project="CORDIC",
             certainty=0.95, source_excerpt="I've been working on the CORDIC pipeline most of the evening"),
    make_raw(ExtractionType.PROGRESS, "Fixed-point stage mostly working", project="CORDIC", certainty=0.75),
    make_raw(ExtractionType.PROBLEM, "Scaling produces unexpected results", project="CORDIC", certainty=0.9),
    make_raw(ExtractionType.POSSIBLE_TASK, "Revisit/investigate CORDIC scaling issue",
             project="CORDIC", certainty=0.35, temporal_context="tomorrow",
             source_excerpt="I think I need to revisit that tomorrow"),
    make_raw(ExtractionType.DEADLINE_CONTEXT, "CORDIC review", project="CORDIC",
             certainty=0.9, temporal_context="Thursday",
             source_excerpt="the review is on Thursday"),
]

_CORDIC_CTX = ExtractionContext(
    now=datetime(2026, 8, 15, 20, 0),
    timezone="UTC",
    projects=["CORDIC"],
    recent_captures=[],
    active_tasks=[],
)


# ─────────────────────────────────────────────────────────────────────────────
# Test A — CORDIC rich capture (12 assertions)
# ─────────────────────────────────────────────────────────────────────────────

class TestSemanticPipeline:

    async def test_A_cordic_rich_capture(self, isolated_vault):
        """12-point invariant: rich capture produces multiple distinct semantic items."""
        from personal_os.core.processor import KnowledgeApplier
        from personal_os.core.vault import VaultManager, VaultConfig

        vault = VaultManager(VaultConfig(root=isolated_vault))
        capture_id = "01JTEST00000000000000000"

        extractor = SemanticExtractor(provider=make_mock_provider(_CORDIC_MOCK))
        result = await extractor.extract(
            _CORDIC_INPUT, context=_CORDIC_CTX,
            capture_id=capture_id, source="Text",
        )

        # 1. source_text == input (identity invariant)
        assert result.source_text == _CORDIC_INPUT

        # 2. at least 4 items extracted
        assert len(result.items) >= 4

        # Collect action types by ExtractionType for assertions 3-8
        by_type: dict[ExtractionType, ExtractionResult] = {}
        for item in result.items:
            by_type[item.raw.type] = item

        # 3. activity → RecordActivity (project=CORDIC so not NoOp)
        act = by_type[ExtractionType.ACTIVITY]
        assert isinstance(act.action, RecordActivity), f"Expected RecordActivity, got {type(act.action)}"

        # 4. progress → RecordProgress (certainty=0.75 ≥ 0.40 threshold)
        prog = by_type[ExtractionType.PROGRESS]
        assert isinstance(prog.action, RecordProgress), f"Expected RecordProgress, got {type(prog.action)}"

        # 5. problem → RecordProblem
        prob = by_type[ExtractionType.PROBLEM]
        assert isinstance(prob.action, RecordProblem), f"Expected RecordProblem, got {type(prob.action)}"

        # 6. possible_task → CreatePossibleTask, NOT CreateTask
        pt = by_type[ExtractionType.POSSIBLE_TASK]
        assert isinstance(pt.action, CreatePossibleTask), f"Expected CreatePossibleTask, got {type(pt.action)}"
        assert not isinstance(pt.action, CreateTask)

        # 7. possible_task temporal_context preserved literally
        assert pt.raw.temporal_context == "tomorrow"

        # 8. deadline_context → NoOp, temporal_context preserved
        dc = by_type[ExtractionType.DEADLINE_CONTEXT]
        assert dc.raw.temporal_context == "Thursday"
        assert isinstance(dc.action, NoOp), (
            f"DEADLINE_CONTEXT must be NoOp, got {type(dc.action)}: {dc.action}"
        )

        # 9. capture_id is a non-empty string
        assert result.capture_id == capture_id
        assert len(result.capture_id) > 0

        # 10. all non-NoOp actions carry the correct capture_id
        for item in result.vault_writes:
            assert item.action.capture_id == capture_id, (
                f"Action {type(item.action).__name__} missing capture_id"
            )

        # 11. to_semantic_block_items() returns all items with required keys
        block_items = result.to_semantic_block_items()
        assert len(block_items) >= 4
        for bi in block_items:
            assert "type" in bi
            assert "description" in bi
            assert "action" in bi

        # 12. KnowledgeApplier.apply_action() on vault_writes does not raise
        applier = KnowledgeApplier(vault)
        for item in result.vault_writes:
            changes = applier.apply_action(item.action)  # must not raise
            assert isinstance(changes, list)

    # ─────────────────────────────────────────────────────────────────────────
    # Test B — Weak intention → possible task
    # ─────────────────────────────────────────────────────────────────────────

    async def test_B_weak_intention_becomes_possible_task(self):
        """Weak commitment captures must produce CreatePossibleTask, not CreateTask."""
        raw = [make_raw(ExtractionType.POSSIBLE_TASK, "Clean up CORDIC assertions",
                        project="CORDIC", certainty=0.25)]
        ctx = ExtractionContext(now=datetime(2026, 8, 15, 20, 0), projects=["CORDIC"])
        extractor = SemanticExtractor(provider=make_mock_provider(raw))
        result = await extractor.extract("I should probably clean up the CORDIC assertions sometime.",
                                         context=ctx, capture_id="01JTEST00000000000000001")

        assert len(result.items) == 1
        action = result.items[0].action
        assert isinstance(action, CreatePossibleTask), f"Expected CreatePossibleTask, got {type(action)}"
        assert not isinstance(action, CreateTask)

    # ─────────────────────────────────────────────────────────────────────────
    # Test C — Strong committed task with deadline normalization
    # ─────────────────────────────────────────────────────────────────────────

    async def test_C_strong_task_with_deadline(self):
        """High-certainty TASK with temporal_context gets normalized deadline."""
        raw = [make_raw(ExtractionType.TASK, "Finish CAD work for IC Design",
                        project="IC Design", certainty=0.93, temporal_context="tomorrow")]
        ctx = ExtractionContext(now=datetime(2026, 8, 15, 20, 0), projects=["IC Design"])
        extractor = SemanticExtractor(provider=make_mock_provider(raw))
        result = await extractor.extract("I need to finish the CAD work for IC Design tomorrow.",
                                         context=ctx, capture_id="01JTEST00000000000000002")

        assert len(result.items) == 1
        item = result.items[0]
        action = item.action

        assert isinstance(action, CreateTask), f"Expected CreateTask, got {type(action)}"
        assert not isinstance(action, CreatePossibleTask)
        assert item.resolved.project == "IC Design"
        assert item.resolved.deadline == date(2026, 8, 16), (
            f"Expected 2026-08-16 (tomorrow from 2026-08-15), got {item.resolved.deadline}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Test D — Contextual completion
    # ─────────────────────────────────────────────────────────────────────────

    async def test_D_contextual_completion(self):
        """Completion with resolved project → MarkComplete."""
        raw = [make_raw(ExtractionType.COMPLETION, "Fixed CORDIC scaling issue",
                        project="CORDIC", certainty=0.95)]
        ctx = ExtractionContext(
            now=datetime(2026, 8, 15, 20, 0),
            projects=["CORDIC"],
            recent_captures=["The CORDIC scaling is still giving me weird results."],
        )
        extractor = SemanticExtractor(provider=make_mock_provider(raw))
        result = await extractor.extract("I finally fixed that scaling problem.",
                                         context=ctx, capture_id="01JTEST00000000000000003")

        assert len(result.items) == 1
        item = result.items[0]
        assert isinstance(item.action, MarkComplete), f"Expected MarkComplete, got {type(item.action)}"
        assert item.resolved.project == "CORDIC"

    # ─────────────────────────────────────────────────────────────────────────
    # Test E — Projectless activity → NoOp / RecordActivity(project=None)
    # ─────────────────────────────────────────────────────────────────────────

    async def test_E_projectless_activity_noop(self):
        """Activity with no resolved project must not write to vault."""
        raw = [make_raw(ExtractionType.ACTIVITY, "Worked on various things", project=None, certainty=0.9)]
        ctx = ExtractionContext(now=datetime(2026, 8, 15, 20, 0), projects=[])
        extractor = SemanticExtractor(provider=make_mock_provider(raw))
        result = await extractor.extract("Worked on various things today.",
                                         context=ctx, capture_id="01JTEST00000000000000004")

        assert len(result.items) == 1
        action = result.items[0].action
        # Either a NoOp or RecordActivity with project=None — both are valid;
        # KnowledgeApplier.apply_action() returns [] for both.
        assert isinstance(action, NoOp) or (isinstance(action, RecordActivity) and action.project is None), (
            f"Expected NoOp or projectless RecordActivity, got {type(action)}: {action}"
        )
        # No vault writes
        assert result.vault_writes == []

    # ─────────────────────────────────────────────────────────────────────────
    # Test F — Semantic block written to journal
    # ─────────────────────────────────────────────────────────────────────────

    async def test_F_semantic_block_written_to_journal(self, isolated_vault):
        """Processing a capture appends a semantic comment block to the daily journal."""
        from personal_os.core.vault import VaultManager, VaultConfig

        vault = VaultManager(VaultConfig(root=isolated_vault))
        capture_id = "01JTESTULID00000000000A"

        # Write raw capture with capture_id
        vault.append_journal_entry("Test capture for semantic block.", capture_id=capture_id)

        # Write semantic block
        items = [{"type": "activity", "description": "test activity", "action": "RecordActivity"}]
        vault.append_semantic_block(capture_id, items)

        # Read journal and verify block present
        journal_path = vault.get_journal_path()
        content = journal_path.read_text()

        assert f"<!-- semantic:{capture_id}" in content, (
            f"Semantic block not found in journal. Content:\n{content}"
        )
        assert capture_id in content
        assert "activity" in content


# ─────────────────────────────────────────────────────────────────────────────
# Test B2 — ActionPlanner certainty demotion (pure unit, synchronous)
# ─────────────────────────────────────────────────────────────────────────────

class TestActionPlannerUnit:
    """Direct ActionPlanner unit tests — no async, no vault, no LLM mock."""

    def setup_method(self):
        self.planner = ActionPlanner()

    def test_task_high_certainty_creates_task(self):
        resolved = make_resolved(ExtractionType.TASK, "Implement synthesis script",
                                 project="CORDIC", certainty=0.8)
        action = self.planner.plan(resolved, capture_id="abc")
        assert isinstance(action, CreateTask), f"Expected CreateTask, got {type(action)}"

    def test_task_low_certainty_demoted_to_possible(self):
        resolved = make_resolved(ExtractionType.TASK, "Maybe fix timing constraints",
                                 project="CORDIC", certainty=0.4)
        action = self.planner.plan(resolved, capture_id="abc")
        assert isinstance(action, CreatePossibleTask), f"Expected CreatePossibleTask, got {type(action)}"
        assert not isinstance(action, CreateTask)

    def test_task_threshold_boundary_below(self):
        # certainty == threshold - epsilon → still demoted
        resolved = make_resolved(ExtractionType.TASK, "Fix thing",
                                 project="CORDIC", certainty=TASK_CERTAINTY_THRESHOLD - 0.01)
        action = self.planner.plan(resolved, capture_id="abc")
        assert isinstance(action, CreatePossibleTask)

    def test_task_threshold_boundary_at(self):
        # certainty == threshold → exact threshold is NOT demoted (>= check)
        resolved = make_resolved(ExtractionType.TASK, "Fix thing",
                                 project="CORDIC", certainty=TASK_CERTAINTY_THRESHOLD)
        action = self.planner.plan(resolved, capture_id="abc")
        assert isinstance(action, CreateTask)

    def test_progress_low_certainty_becomes_activity(self):
        resolved = make_resolved(ExtractionType.PROGRESS, "Pipeline might be working",
                                 project="CORDIC", certainty=0.2)
        action = self.planner.plan(resolved, capture_id="abc")
        assert isinstance(action, RecordActivity), f"Expected RecordActivity, got {type(action)}"

    def test_progress_high_certainty_records_progress(self):
        resolved = make_resolved(ExtractionType.PROGRESS, "Pipeline definitely working",
                                 project="CORDIC", certainty=0.8)
        action = self.planner.plan(resolved, capture_id="abc")
        assert isinstance(action, RecordProgress), f"Expected RecordProgress, got {type(action)}"

    def test_activity_no_project_gives_noop(self):
        resolved = make_resolved(ExtractionType.ACTIVITY, "Worked on stuff", project=None, certainty=0.9)
        action = self.planner.plan(resolved, capture_id="abc")
        # ActionPlanner returns NoOp directly for projectless activity (Daily Stream is sufficient)
        assert isinstance(action, NoOp)
        assert "activity with no resolved project" in action.reason

    def test_deadline_context_is_noop(self):
        resolved = make_resolved(ExtractionType.DEADLINE_CONTEXT, "Review on Thursday",
                                 project="CORDIC", certainty=0.9)
        action = self.planner.plan(resolved, capture_id="abc")
        assert isinstance(action, NoOp), f"DEADLINE_CONTEXT must be NoOp, got {type(action)}"

    def test_possible_task_type_becomes_create_possible(self):
        resolved = make_resolved(ExtractionType.POSSIBLE_TASK, "Should clean up assertions",
                                 project="CORDIC", certainty=0.25)
        action = self.planner.plan(resolved, capture_id="abc")
        assert isinstance(action, CreatePossibleTask)

    def test_capture_id_propagated_to_action(self):
        resolved = make_resolved(ExtractionType.TASK, "Write tests", project="CORDIC", certainty=0.9)
        action = self.planner.plan(resolved, capture_id="MY_ULID_123")
        assert action.capture_id == "MY_ULID_123"

    def test_journal_type_is_noop(self):
        resolved = make_resolved(ExtractionType.JOURNAL, "Feeling good today", project=None)
        action = self.planner.plan(resolved, capture_id="abc")
        assert isinstance(action, NoOp)
