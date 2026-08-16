"""Project matching abstraction for deterministic project resolution.

This module defines the ProjectMatcher interface and a stdlib-only implementation.
The abstraction allows swapping matching algorithms (difflib, RapidFuzz, etc.)
without changing ProjectResolver.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from personal_os.core.extraction.models import ProjectResolution


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Result of matching a project reference against known projects.

    Attributes:
        project_name: The matched canonical project name, or None if no match.
        resolution: How the match was determined.
        score: Match confidence score (0.0-1.0), higher is better.
        candidates: All candidate matches for ambiguity detection.
    """
    project_name: Optional[str]
    resolution: ProjectResolution
    score: float
    candidates: tuple[tuple[str, float], ...] = ()


class ProjectMatcher(ABC):
    """Abstract interface for project matching algorithms.

    Implementations must be deterministic and side-effect free.
    """

    @abstractmethod
    def match(self, query: str, projects: dict[str, str]) -> MatchResult:
        """Match a query string against known projects.

        Args:
            query: The project reference string to match (lowercased).
            projects: Dict mapping lowercased project name -> canonical project name.

        Returns:
            MatchResult with the best match and resolution classification.
        """
        pass

    @abstractmethod
    def score(self, a: str, b: str) -> float:
        """Compute similarity score between two strings (0.0-1.0)."""
        pass


class SimpleProjectMatcher(ProjectMatcher):
    """Stdlib-only project matcher using substring and difflib.

    Matching priority:
    1. Exact case-insensitive match
    2. Substring match (query in project or project in query)
    3. difflib.SequenceMatcher similarity above threshold

    This is a baseline implementation; can be replaced with RapidFuzz for better accuracy.
    """

    def __init__(
        self,
        exact_threshold: float = 1.0,
        substring_threshold: float = 0.85,
        fuzzy_threshold: float = 0.75,
        ambiguity_gap: float = 0.10,
    ):
        """Initialize matcher with configurable thresholds.

        Args:
            exact_threshold: Score for exact match (always 1.0).
            substring_threshold: Score assigned to substring matches.
            fuzzy_threshold: Minimum difflib ratio for fuzzy match.
            ambiguity_gap: Minimum score gap between top 2 candidates to avoid AMBIGUOUS.
        """
        self.exact_threshold = exact_threshold
        self.substring_threshold = substring_threshold
        self.fuzzy_threshold = fuzzy_threshold
        self.ambiguity_gap = ambiguity_gap

    def match(self, query: str, projects: dict[str, str]) -> MatchResult:
        """Match query against projects using priority order."""
        query_lower = query.lower().strip()
        if not query_lower:
            return MatchResult(None, ProjectResolution.UNKNOWN, 0.0)

        candidates = []

        # 1. Exact match
        if query_lower in projects:
            return MatchResult(
                project_name=projects[query_lower],
                resolution=ProjectResolution.EXISTING,
                score=1.0,
                candidates=((projects[query_lower], 1.0),)
            )

        # 2. Substring match (bidirectional)
        for proj_lower, canonical in projects.items():
            if query_lower in proj_lower or proj_lower in query_lower:
                # Score based on length ratio, but ensure it's above fuzzy_threshold
                ratio = min(len(query_lower), len(proj_lower)) / max(len(query_lower), len(proj_lower))
                score = max(self.substring_threshold * ratio, self.fuzzy_threshold)
                candidates.append((canonical, score))

        # 3. Fuzzy match via difflib
        import difflib
        for proj_lower, canonical in projects.items():
            ratio = difflib.SequenceMatcher(None, query_lower, proj_lower).ratio()
            if ratio >= self.fuzzy_threshold:
                candidates.append((canonical, ratio))

        # Deduplicate by canonical name, keep highest score
        best_scores = {}
        for canonical, score in candidates:
            if canonical not in best_scores or score > best_scores[canonical]:
                best_scores[canonical] = score

        if not best_scores:
            return MatchResult(None, ProjectResolution.UNKNOWN, 0.0, candidates=())

        # Sort by score descending
        sorted_candidates = sorted(best_scores.items(), key=lambda x: -x[1])
        top_canonical, top_score = sorted_candidates[0]

        # Check for ambiguity
        if len(sorted_candidates) >= 2:
            second_score = sorted_candidates[1][1]
            if top_score - second_score < self.ambiguity_gap:
                return MatchResult(
                    project_name=None,
                    resolution=ProjectResolution.AMBIGUOUS,
                    score=top_score,
                    candidates=tuple(sorted_candidates)
                )

        # Single best candidate above fuzzy threshold
        if top_score >= self.fuzzy_threshold:
            return MatchResult(
                project_name=top_canonical,
                resolution=ProjectResolution.FUZZY,
                score=top_score,
                candidates=tuple(sorted_candidates)
            )

        return MatchResult(None, ProjectResolution.UNKNOWN, top_score, candidates=tuple(sorted_candidates))

    def score(self, a: str, b: str) -> float:
        """Compute difflib similarity ratio."""
        import difflib
        return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()