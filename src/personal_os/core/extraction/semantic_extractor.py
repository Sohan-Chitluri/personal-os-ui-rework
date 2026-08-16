"""SemanticExtractor — central pipeline orchestrator.

Connects LLMProvider → Validator → ProjectResolver → ActionPlanner and
returns a CaptureResult that preserves the original text alongside all
semantic interpretations.

Architecture:
    SemanticExtractor.extract(text, context, capture_id)
        ↓
    LLMProvider.extract()     — probabilistic, async, provider-agnostic
        ↓
    ExtractionValidator       — structural validation, no semantics
        ↓
    ProjectResolver           — deterministic project matching
        ↓
    _normalize_deadline()     — date normalization using context.now
        ↓
    ActionPlanner.plan()      — deterministic VaultAction selection
        ↓
    CaptureResult             — source_text preserved, items list, errors

source_text is stored exactly as received. It is never summarized, rewritten,
or replaced. The semantic interpretation is additive — it adds understanding
without destroying the original words.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional

from personal_os.core.extraction.action_planner import ActionPlanner
from personal_os.core.extraction.actions import NoOp, VaultAction
from personal_os.core.extraction.contexts import ExtractionContext
from personal_os.core.extraction.models import (
    DeadlineKind,
    DeadlineResolution,
    ExtractionType,
    ProjectResolution,
    RawExtraction,
    ResolvedExtraction,
)
from personal_os.core.extraction.resolver import ProjectResolver
from personal_os.core.extraction.validator import ExtractionValidator, ValidationResult
from personal_os.core.llm import LLMProvider, LLMProviderError

log = logging.getLogger(__name__)

# Weekday name → index (Monday=0)
_WEEKDAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


@dataclass
class ExtractionResult:
    """One semantic item extracted from a capture, fully processed.

    Attributes:
        raw: As returned by the LLM — before validation or resolution.
        resolved: After project resolution and deadline normalization.
        action: The VaultAction this item maps to. NoOp = no vault write needed;
            the raw capture already carries this information in the Daily Stream.
        validation: Structural validation result (may contain warnings).
    """
    raw: RawExtraction
    resolved: ResolvedExtraction
    action: VaultAction
    validation: ValidationResult


@dataclass
class CaptureResult:
    """Result of processing one capture through the full semantic pipeline.

    source_text is the user's exact words, preserved unchanged. It is the
    canonical record. items is the additive semantic layer — what Hermes
    understood from those words.

    Attributes:
        source_text: Original capture text — immutable, never transformed.
        capture_id: ULID generated at capture time; links to Daily Stream entry.
        items: All extracted semantic items (some may map to NoOp).
        errors: Non-fatal pipeline errors (e.g. one bad LLM item, rest proceed).
    """
    source_text: str
    capture_id: str
    items: list[ExtractionResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def vault_writes(self) -> list[ExtractionResult]:
        """Items that produce actual vault changes (non-NoOp actions)."""
        return [i for i in self.items if not isinstance(i.action, NoOp)]

    def to_semantic_block_items(self) -> list[dict[str, Any]]:
        """Serialize all items for vault.append_semantic_block().

        This representation is stored as YAML in an HTML comment block in the
        daily journal file — machine-parseable, human-readable, never replaces
        the raw capture text.
        """
        result = []
        for item in self.items:
            result.append({
                "type": item.raw.type.value,
                "description": item.raw.description,
                "project": item.resolved.project,
                "certainty": round(item.raw.certainty, 3),
                "confidence": round(item.raw.confidence, 3),
                "temporal_context": item.raw.temporal_context,
                "source_excerpt": item.raw.source_excerpt,
                "action": type(item.action).__name__,
            })
        return result


class SemanticExtractor:
    """Orchestrates the full semantic extraction pipeline for one capture.

    Inject dependencies at construction time; call extract() for each capture.
    Thread-safe for reads (no mutable state modified after __init__).
    """

    def __init__(
        self,
        provider: LLMProvider,
        validator: Optional[ExtractionValidator] = None,
        resolver: Optional[ProjectResolver] = None,
        planner: Optional[ActionPlanner] = None,
    ) -> None:
        self._provider = provider
        self._validator = validator or ExtractionValidator()
        self._resolver = resolver or ProjectResolver()
        self._planner = planner or ActionPlanner()

    async def extract(
        self,
        text: str,
        context: ExtractionContext,
        capture_id: str = "",
        source: str = "Text",
    ) -> CaptureResult:
        """Process one capture through the full semantic pipeline.

        Args:
            text: The user's raw capture text. Preserved in source_text; never
                modified, summarized, or used as a mutable buffer.
            context: Runtime context (current time, known projects, recent captures).
            capture_id: ULID of the journal entry for traceability.
            source: Origin label ("Text", "Voice", "CLI", etc.).

        Returns:
            CaptureResult with source_text == text (identity invariant).
        """
        result = CaptureResult(source_text=text, capture_id=capture_id)

        try:
            raw_extractions = await self._provider.extract(
                text, context=context.to_llm_dict()
            )
        except LLMProviderError as exc:
            result.errors.append(f"LLM extraction failed: {exc}")
            return result

        # Build the vault project list once for resolver.
        # Resolver accepts list[dict] with 'name' key.
        project_dicts = [{"name": p} for p in context.projects]

        for raw in raw_extractions:
            try:
                item = self._process_one(
                    raw, project_dicts, context, source, capture_id
                )
                result.items.append(item)
            except Exception as exc:
                log.warning("Failed to process extraction item %r: %s", raw, exc)
                result.errors.append(f"Item processing error: {exc}")

        return result

    # ------------------------------------------------------------------ private

    def _process_one(
        self,
        raw: RawExtraction,
        project_dicts: list[dict],
        context: ExtractionContext,
        source: str,
        capture_id: str,
    ) -> ExtractionResult:
        # 1. Structural validation
        validation = self._validator.validate(raw)
        if not validation.is_valid:
            raise ValueError(
                f"Invalid extraction ({validation.error_count} errors): "
                f"{[e.message for e in validation.errors if e.severity.value == 'error']}"
            )

        # 2. Project resolution
        resolved_proj = self._resolver.resolve(raw.project_ref, project_dicts)

        # 3. Deadline normalization (only for TASK type with temporal_context)
        resolved_date, date_resolution = self._normalize_deadline(raw, context)
        deadline_raw = raw.deadline.raw_text if raw.deadline else raw.temporal_context

        # 4. Build ResolvedExtraction
        resolved = ResolvedExtraction(
            type=raw.type,
            description=raw.description,
            project=resolved_proj.project,
            project_resolution=resolved_proj.resolution,
            deadline=resolved_date,
            deadline_resolution=date_resolution,
            deadline_raw=deadline_raw,
            confidence=raw.confidence,
            certainty=raw.certainty,
            source_excerpt=raw.source_excerpt,
            temporal_context=raw.temporal_context,
            metadata={
                **(raw.metadata or {}),
                "project_score": resolved_proj.score,
                "project_candidates": list(resolved_proj.candidates),
            },
        )

        # 5. Plan the vault action
        action = self._planner.plan(resolved, source=source, capture_id=capture_id)

        return ExtractionResult(raw=raw, resolved=resolved, action=action, validation=validation)

    def _normalize_deadline(
        self,
        raw: RawExtraction,
        context: ExtractionContext,
    ) -> tuple[Optional[date], DeadlineResolution]:
        """Normalize a temporal expression to an absolute date using context.now.

        Only called when extraction type is TASK — other temporal mentions
        (DEADLINE_CONTEXT, ACTIVITY, etc.) stay as temporal_context strings.
        """
        if raw.type != ExtractionType.TASK:
            return None, DeadlineResolution.NONE

        # Prefer explicit RawDeadline if present
        if raw.deadline and raw.deadline.kind == DeadlineKind.EXPLICIT_DATE:
            import re
            m = re.match(r'(\d{4})-(\d{2})-(\d{2})', raw.deadline.raw_text)
            if m:
                try:
                    resolved_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                    return resolved_date, DeadlineResolution.RESOLVED
                except ValueError:
                    pass

        # Fall back to temporal_context string
        temporal = raw.temporal_context or (raw.deadline.raw_text if raw.deadline else None)
        if not temporal:
            return None, DeadlineResolution.NONE

        normalized = self._parse_relative_temporal(temporal, context.now)
        if normalized:
            return normalized, DeadlineResolution.RESOLVED

        # Contextual ("after class", "before lab") — store as deferred
        return None, DeadlineResolution.DEFERRED

    @staticmethod
    def _parse_relative_temporal(text: str, now: datetime) -> Optional[date]:
        """Parse common relative temporal expressions to absolute dates."""
        text_lower = text.lower().strip()
        today = now.date()

        if text_lower in ("today", "eod", "end of day"):
            return today
        if text_lower == "tomorrow":
            return today + timedelta(days=1)
        if text_lower == "yesterday":
            return today - timedelta(days=1)

        # "next Monday", "this Friday", "Thursday"
        for name, idx in _WEEKDAY_MAP.items():
            if name in text_lower:
                days_ahead = (idx - today.weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7  # "Thursday" when today is Thursday → next Thursday
                return today + timedelta(days=days_ahead)

        # "next week"
        if "next week" in text_lower:
            return today + timedelta(weeks=1)

        # ISO date in the string
        import re
        m = re.search(r'(\d{4})-(\d{2})-(\d{2})', text)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass

        return None
