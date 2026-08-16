"""Deterministic project resolution for extraction pipeline.

This module implements ProjectResolver which converts RawProjectRef into
a canonical project name and ProjectResolution classification.
No side effects, no vault modifications, no policy decisions.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from personal_os.core.extraction.models import (
    RawProjectRef,
    ProjectResolution,
    ExtractionType,
)
from personal_os.core.extraction.project_matcher import ProjectMatcher, SimpleProjectMatcher


@dataclass(frozen=True, slots=True)
class ResolvedProject:
    """Result of resolving a RawProjectRef.

    Attributes:
        project: Canonical project name if resolved, None otherwise.
        resolution: How the reference was classified.
        score: Match confidence (0.0-1.0).
        raw_name: Original project reference name for audit.
        is_implicit: Whether the reference was implicit (context-dependent).
        candidates: All candidates considered for ambiguity detection.
    """
    project: Optional[str]
    resolution: ProjectResolution
    score: float
    raw_name: str
    is_implicit: bool
    candidates: tuple[tuple[str, float], ...] = ()


class ProjectResolver:
    """Deterministic project resolution from RawProjectRef.

    Uses a ProjectMatcher for algorithmic matching, applies policy thresholds
    for NEW vs UNKNOWN classification. Pure function — no side effects.
    """

    # Policy thresholds
    NEW_PROJECT_CONFIDENCE_THRESHOLD = 0.80
    IMPLICIT_NEW_THRESHOLD = 0.90  # Higher bar for implicit references

    def __init__(
        self,
        matcher: Optional[ProjectMatcher] = None,
        aliases: Optional[dict[str, str]] = None,
    ):
        """Initialize resolver.

        Args:
            matcher: ProjectMatcher implementation (default: SimpleProjectMatcher).
            aliases: Mapping from alias (lowercased) -> canonical project name.
        """
        self._matcher = matcher or SimpleProjectMatcher()
        self._aliases = {k.lower(): v for k, v in (aliases or {}).items()}

    def resolve(self, ref: Optional[RawProjectRef], projects: list[dict]) -> ResolvedProject:
        """Resolve a RawProjectRef against known projects.

        Args:
            ref: The project reference to resolve (None = no project mentioned).
            projects: List of project dicts from VaultManager.list_projects().

        Returns:
            ResolvedProject with canonical name and resolution classification.
        """
        if ref is None:
            return ResolvedProject(
                project=None,
                resolution=ProjectResolution.UNKNOWN,
                score=0.0,
                raw_name="",
                is_implicit=False,
                candidates=(),
            )

        raw_name = ref.name.strip()
        is_implicit = ref.is_implicit
        ref_confidence = ref.confidence

        if not raw_name:
            return ResolvedProject(
                project=None,
                resolution=ProjectResolution.UNKNOWN,
                score=0.0,
                raw_name=raw_name,
                is_implicit=is_implicit,
                candidates=(),
            )

        # Build project lookup: lowercased name -> canonical name
        project_lookup = {p['name'].lower(): p['name'] for p in projects}
        project_names = {p['name'] for p in projects}

        # 1. Check CASE-SENSITIVE exact match first
        if raw_name in project_names:
            return ResolvedProject(
                project=raw_name,
                resolution=ProjectResolution.EXISTING,
                score=1.0,
                raw_name=raw_name,
                is_implicit=is_implicit,
                candidates=((raw_name, 1.0),)
            )

        # 2. Check ALIASES (before case-insensitive match, so "maestro" -> ALIAS not EXISTING)
        alias_match = self._resolve_alias(raw_name, project_lookup)
        if alias_match:
            return alias_match

        # 3. Check case-insensitive exact match (EXISTING)
        raw_lower = raw_name.lower()
        if raw_lower in project_lookup:
            canonical = project_lookup[raw_lower]
            return ResolvedProject(
                project=canonical,
                resolution=ProjectResolution.EXISTING,
                score=1.0,
                raw_name=raw_name,
                is_implicit=is_implicit,
                candidates=((canonical, 1.0),)
            )

        # 4. Delegate to matcher for fuzzy matching
        match_result = self._matcher.match(raw_name, project_lookup)

        # 5. Apply policy for NEW vs UNKNOWN
        return self._apply_policy(match_result, raw_name, is_implicit, ref_confidence)

    def _resolve_alias(self, raw_name: str, project_lookup: dict[str, str]) -> Optional[ResolvedProject]:
        """Check if raw_name matches a configured alias."""
        raw_lower = raw_name.lower()
        if raw_lower in self._aliases:
            canonical = self._aliases[raw_lower]
            # Verify the canonical project actually exists
            if canonical.lower() in project_lookup:
                return ResolvedProject(
                    project=canonical,
                    resolution=ProjectResolution.ALIAS,
                    score=1.0,
                    raw_name=raw_name,
                    is_implicit=False,
                    candidates=((canonical, 1.0),)
                )
            # Canonical project doesn't exist - falls through to UNKNOWN
            return ResolvedProject(
                project=None,
                resolution=ProjectResolution.UNKNOWN,
                score=0.0,
                raw_name=raw_name,
                is_implicit=False,
                candidates=(),
            )
        return None

    def _apply_policy(
        self,
        match_result,
        raw_name: str,
        is_implicit: bool,
        ref_confidence: float,
    ) -> ResolvedProject:
        """Apply NEW/UNKNOWN policy based on match result and reference confidence."""
        resolution = match_result.resolution
        project = match_result.project_name
        score = match_result.score
        candidates = match_result.candidates

        # If matcher found a concrete match (EXISTING or FUZZY), use it
        if resolution in (ProjectResolution.EXISTING, ProjectResolution.FUZZY):
            return ResolvedProject(
                project=project,
                resolution=resolution,
                score=score,
                raw_name=raw_name,
                is_implicit=is_implicit,
                candidates=candidates,
            )

        # If AMBIGUOUS, return as-is (no project)
        if resolution == ProjectResolution.AMBIGUOUS:
            return ResolvedProject(
                project=None,
                resolution=ProjectResolution.AMBIGUOUS,
                score=score,
                raw_name=raw_name,
                is_implicit=is_implicit,
                candidates=candidates,
            )

        # UNKNOWN from matcher — check if we should create NEW project
        # Require high reference confidence; implicit refs need higher threshold
        threshold = self.IMPLICIT_NEW_THRESHOLD if is_implicit else self.NEW_PROJECT_CONFIDENCE_THRESHOLD

        if ref_confidence >= threshold:
            return ResolvedProject(
                project=raw_name,  # Use raw name as canonical for new project
                resolution=ProjectResolution.NEW,
                score=ref_confidence,
                raw_name=raw_name,
                is_implicit=is_implicit,
                candidates=(),
            )

        return ResolvedProject(
            project=None,
            resolution=ProjectResolution.UNKNOWN,
            score=ref_confidence,
            raw_name=raw_name,
            is_implicit=is_implicit,
            candidates=(),
        )


# Default aliases (can be extended via config)
DEFAULT_ALIASES = {
    "maestro": "Maestro",
    "router": "Maestro",
    "drone": "Drone",
    "blindguard": "BlindGuard",
    "vlsi": "VLSI",
    "verilog": "VLSI",
    "fpga": "FPGA",
    "namo": "Team-NAMO",
    "team namo": "Team-NAMO",
}