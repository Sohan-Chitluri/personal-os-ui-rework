"""Unit tests for ProjectMatcher and ProjectResolver."""
import pytest
from personal_os.core.extraction.models import ProjectResolution, RawProjectRef
from personal_os.core.extraction.project_matcher import (
    ProjectMatcher,
    SimpleProjectMatcher,
    MatchResult,
)
from personal_os.core.extraction.resolver import ProjectResolver, ResolvedProject, DEFAULT_ALIASES


# ===== Fixtures =====
@pytest.fixture
def sample_projects():
    """Sample project list from VaultManager."""
    return [
        {"name": "Maestro", "path": "/projects/Maestro", "status": "active"},
        {"name": "Drone", "path": "/projects/Drone", "status": "active"},
        {"name": "VLSI", "path": "/projects/VLSI", "status": "active"},
        {"name": "FPGA", "path": "/projects/FPGA", "status": "active"},
        {"name": "Team-NAMO", "path": "/projects/Team-NAMO", "status": "active"},
    ]


@pytest.fixture
def matcher():
    """Default SimpleProjectMatcher."""
    return SimpleProjectMatcher()


@pytest.fixture
def resolver():
    """Default ProjectResolver with standard aliases."""
    return ProjectResolver(aliases=DEFAULT_ALIASES)


# ===== SimpleProjectMatcher Tests =====
class TestSimpleProjectMatcher:
    """Tests for SimpleProjectMatcher matching logic."""

    def test_exact_match_case_insensitive(self, matcher, sample_projects):
        """Exact match should return EXISTING with score 1.0."""
        for proj in sample_projects:
            # Test various casings
            for query in [proj['name'], proj['name'].lower(), proj['name'].upper(), proj['name'].title()]:
                result = matcher.match(query, {p['name'].lower(): p['name'] for p in sample_projects})
                assert result.resolution == ProjectResolution.EXISTING
                assert result.project_name == proj['name']
                assert result.score == 1.0

    def test_exact_match_nonexistent(self, matcher, sample_projects):
        """Non-existent project returns UNKNOWN."""
        projects = {p['name'].lower(): p['name'] for p in sample_projects}
        result = matcher.match("Nonexistent", projects)
        assert result.resolution == ProjectResolution.UNKNOWN
        assert result.project_name is None

    def test_substring_match_query_in_project(self, matcher, sample_projects):
        """Substring query contained in project name."""
        projects = {p['name'].lower(): p['name'] for p in sample_projects}
        result = matcher.match("maest", projects)  # "maest" in "maestro"
        assert result.resolution == ProjectResolution.FUZZY
        assert result.project_name == "Maestro"

    def test_substring_match_project_in_query(self, matcher, sample_projects):
        """Project name contained in query."""
        projects = {p['name'].lower(): p['name'] for p in sample_projects}
        result = matcher.match("maestro router", projects)  # "maestro" in query
        assert result.resolution == ProjectResolution.FUZZY
        assert result.project_name == "Maestro"

    def test_fuzzy_match_difflib(self, matcher, sample_projects):
        """Difflib fuzzy match above threshold."""
        projects = {p['name'].lower(): p['name'] for p in sample_projects}
        result = matcher.match("maestero", projects)  # typo
        assert result.resolution in (ProjectResolution.FUZZY, ProjectResolution.EXISTING)
        assert result.project_name == "Maestro"

    def test_fuzzy_match_below_threshold(self, matcher, sample_projects):
        """Fuzzy match below threshold returns UNKNOWN."""
        projects = {p['name'].lower(): p['name'] for p in sample_projects}
        result = matcher.match("completelydifferent", projects)
        assert result.resolution == ProjectResolution.UNKNOWN
        assert result.project_name is None

    def test_ambiguous_match_two_similar(self, matcher):
        """Two similar candidates within ambiguity gap returns AMBIGUOUS."""
        projects = {"project alpha": "Project Alpha", "project beta": "Project Beta"}
        # Query similar to both
        result = matcher.match("project", projects)
        assert result.resolution == ProjectResolution.AMBIGUOUS
        assert result.project_name is None
        assert len(result.candidates) >= 2

    def test_empty_query_returns_unknown(self, matcher):
        """Empty query returns UNKNOWN."""
        result = matcher.match("", {})
        assert result.resolution == ProjectResolution.UNKNOWN
        assert result.project_name is None

    def test_score_method(self, matcher):
        """Score method returns difflib ratio."""
        score = matcher.score("maestro", "maestro")
        assert score == 1.0
        score = matcher.score("maestro", "maestero")
        assert 0.8 < score < 1.0
        score = matcher.score("abc", "xyz")
        assert score < 0.5


# ===== ProjectResolver Tests =====
class TestProjectResolver:
    """Tests for ProjectResolver resolution logic."""

    def test_exact_match(self, resolver, sample_projects):
        """Exact project name resolves to EXISTING."""
        ref = RawProjectRef(name="Maestro", confidence=0.9)
        result = resolver.resolve(ref, sample_projects)
        assert result.resolution == ProjectResolution.EXISTING
        assert result.project == "Maestro"
        assert result.score == 1.0

    def test_case_insensitive_exact_match(self, sample_projects):
        """Case-insensitive exact match works (resolver without aliases)."""
        resolver_no_aliases = ProjectResolver()
        ref = RawProjectRef(name="maestro", confidence=0.9)
        result = resolver_no_aliases.resolve(ref, sample_projects)
        assert result.resolution == ProjectResolution.EXISTING
        assert result.project == "Maestro"

    def test_alias_match(self, resolver, sample_projects):
        """Configured alias resolves to ALIAS."""
        ref = RawProjectRef(name="maestro", confidence=0.9)
        result = resolver.resolve(ref, sample_projects)
        assert result.resolution == ProjectResolution.ALIAS
        assert result.project == "Maestro"
        assert result.score == 1.0

    def test_alias_match_various(self, resolver, sample_projects):
        """Multiple aliases work."""
        for alias in ["router", "drone", "vlsi", "verilog", "fpga", "namo", "team namo"]:
            ref = RawProjectRef(name=alias, confidence=0.9)
            result = resolver.resolve(ref, sample_projects)
            assert result.resolution == ProjectResolution.ALIAS, f"Alias '{alias}' failed"

    def test_fuzzy_match(self, resolver, sample_projects):
        """Fuzzy match returns FUZZY resolution."""
        ref = RawProjectRef(name="maestero", confidence=0.85)  # typo
        result = resolver.resolve(ref, sample_projects)
        assert result.resolution == ProjectResolution.FUZZY
        assert result.project == "Maestro"

    def test_ambiguous_match(self, resolver):
        """Ambiguous match returns AMBIGUOUS."""
        projects = [
            {"name": "Project Alpha"},
            {"name": "Project Beta"},
        ]
        ref = RawProjectRef(name="project", confidence=0.85)
        result = resolver.resolve(ref, projects)
        assert result.resolution == ProjectResolution.AMBIGUOUS
        assert result.project is None
        assert len(result.candidates) >= 2

    def test_unknown_project_low_confidence(self, resolver, sample_projects):
        """Unknown project with low confidence returns UNKNOWN."""
        ref = RawProjectRef(name="RandomProject", confidence=0.5)
        result = resolver.resolve(ref, sample_projects)
        assert result.resolution == ProjectResolution.UNKNOWN
        assert result.project is None

    def test_new_project_high_confidence_explicit(self, resolver, sample_projects):
        """High confidence explicit reference creates NEW."""
        ref = RawProjectRef(name="Vortex", confidence=0.9, is_implicit=False)
        result = resolver.resolve(ref, sample_projects)
        assert result.resolution == ProjectResolution.NEW
        assert result.project == "Vortex"
        assert result.score == 0.9

    def test_new_project_below_threshold(self, resolver, sample_projects):
        """Confidence below threshold returns UNKNOWN not NEW."""
        ref = RawProjectRef(name="Vortex", confidence=0.75, is_implicit=False)
        result = resolver.resolve(ref, sample_projects)
        assert result.resolution == ProjectResolution.UNKNOWN
        assert result.project is None

    def test_implicit_reference_requires_higher_threshold(self, resolver, sample_projects):
        """Implicit references need higher confidence for NEW."""
        # At 0.85, explicit would be NEW but implicit is UNKNOWN
        ref = RawProjectRef(name="Vortex", confidence=0.85, is_implicit=True)
        result = resolver.resolve(ref, sample_projects)
        assert result.resolution == ProjectResolution.UNKNOWN

        # At 0.90+, implicit becomes NEW
        ref = RawProjectRef(name="Vortex", confidence=0.92, is_implicit=True)
        result = resolver.resolve(ref, sample_projects)
        assert result.resolution == ProjectResolution.NEW

    def test_none_ref_returns_unknown(self, resolver):
        """None reference returns UNKNOWN."""
        result = resolver.resolve(None, [])
        assert result.resolution == ProjectResolution.UNKNOWN
        assert result.project is None

    def test_empty_name_returns_unknown(self, resolver):
        """Empty project name returns UNKNOWN."""
        ref = RawProjectRef(name="", confidence=0.9)
        result = resolver.resolve(ref, [])
        assert result.resolution == ProjectResolution.UNKNOWN

    def test_whitespace_name_returns_unknown(self, resolver):
        """Whitespace-only name returns UNKNOWN."""
        ref = RawProjectRef(name="   ", confidence=0.9)
        result = resolver.resolve(ref, [])
        assert result.resolution == ProjectResolution.UNKNOWN

    def test_preserves_raw_name_and_implicit(self, resolver, sample_projects):
        """ResolvedProject preserves raw_name and is_implicit for audit."""
        ref = RawProjectRef(name="Vortex", confidence=0.9, is_implicit=True)
        result = resolver.resolve(ref, sample_projects)
        assert result.raw_name == "Vortex"
        assert result.is_implicit is True

    def test_candidates_included_for_ambiguous(self, resolver):
        """AMBIGUOUS resolution includes candidates for debugging."""
        projects = [
            {"name": "Project Alpha"},
            {"name": "Project Beta"},
            {"name": "Project Gamma"},
        ]
        ref = RawProjectRef(name="project", confidence=0.85)
        result = resolver.resolve(ref, projects)
        assert result.resolution == ProjectResolution.AMBIGUOUS
        assert len(result.candidates) >= 2


# ===== Integration: Matcher + Resolver =====
class TestMatcherResolverIntegration:
    """Tests combining matcher and resolver."""

    def test_custom_matcher_injected(self, sample_projects):
        """Custom matcher can be injected into resolver."""
        custom_matcher = SimpleProjectMatcher(fuzzy_threshold=0.95)
        resolver = ProjectResolver(matcher=custom_matcher)
        # Low confidence so it doesn't trigger NEW project promotion (threshold 0.80)
        ref = RawProjectRef(name="maestero", confidence=0.5)
        result = resolver.resolve(ref, sample_projects)
        # With higher fuzzy threshold and low confidence, should be UNKNOWN not FUZZY
        assert result.resolution == ProjectResolution.UNKNOWN

    def test_custom_aliases(self, sample_projects):
        """Custom aliases work."""
        custom_aliases = {"myproject": "Maestro"}
        resolver = ProjectResolver(aliases=custom_aliases)
        ref = RawProjectRef(name="myproject", confidence=0.9)
        result = resolver.resolve(ref, sample_projects)
        assert result.resolution == ProjectResolution.ALIAS
        assert result.project == "Maestro"


# ===== Edge Cases =====
class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_new_project_name_collision_with_existing(self, resolver, sample_projects):
        """NEW project with name matching existing should not happen (exact match first)."""
        ref = RawProjectRef(name="Maestro", confidence=0.95)
        result = resolver.resolve(ref, sample_projects)
        assert result.resolution == ProjectResolution.EXISTING  # Not NEW

    def test_alias_takes_precedence_over_fuzzy(self, resolver, sample_projects):
        """Alias match wins over fuzzy match."""
        ref = RawProjectRef(name="maestro", confidence=0.7)  # Low confidence but alias
        result = resolver.resolve(ref, sample_projects)
        assert result.resolution == ProjectResolution.ALIAS
        assert result.score == 1.0

    def test_unknown_alias_falls_through(self, resolver, sample_projects):
        """Alias for non-existent project falls through to fuzzy/unknown."""
        resolver = ProjectResolver(aliases={"ghost": "NonExistent"})
        ref = RawProjectRef(name="ghost", confidence=0.9)
        result = resolver.resolve(ref, sample_projects)
        # Canonical "NonExistent" not in projects, so falls through
        assert result.resolution == ProjectResolution.UNKNOWN


# ===== Threshold Configuration =====
class TestThresholds:
    """Tests for configurable thresholds."""

    def test_fuzzy_threshold_config(self, sample_projects):
        """Fuzzy threshold can be configured."""
        strict = SimpleProjectMatcher(fuzzy_threshold=0.95)
        loose = SimpleProjectMatcher(fuzzy_threshold=0.7)

        projects = {p['name'].lower(): p['name'] for p in sample_projects}

        # "maestero" typo
        result_strict = strict.match("maestero", projects)
        result_loose = loose.match("maestero", projects)

        # Loose should match, strict may not
        assert result_loose.resolution in (ProjectResolution.FUZZY, ProjectResolution.EXISTING)
        # Both should at least find some candidate
        assert result_loose.project_name is not None or result_strict.project_name is not None

    def test_ambiguity_gap_config(self):
        """Ambiguity gap can be configured."""
        strict = SimpleProjectMatcher(ambiguity_gap=0.2)
        loose = SimpleProjectMatcher(ambiguity_gap=0.01)

        projects = {"project alpha": "Project Alpha", "project beta": "Project Beta"}

        result_strict = strict.match("project", projects)
        result_loose = loose.match("project", projects)

        # Loose gap -> more likely ambiguous
        assert result_loose.resolution == ProjectResolution.AMBIGUOUS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])