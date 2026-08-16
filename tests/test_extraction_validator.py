"""Unit tests for ExtractionValidator.

Tests cover:
- valid extraction
- missing description
- invalid confidence
- invalid project reference
- invalid deadline
- multiple simultaneous validation errors
"""
import pytest
from datetime import date
from personal_os.core.extraction.models import (
    ExtractionType,
    DeadlineKind,
    RawDeadline,
    RawProjectRef,
    RawExtraction,
)
from personal_os.core.extraction.validator import (
    ExtractionValidator,
    ValidationResult,
    ValidationSeverity,
    validate_extraction,
)


class TestExtractionValidator:
    """Tests for the ExtractionValidator class."""

    def setup_method(self):
        self.validator = ExtractionValidator()

    def make_valid_extraction(self, **overrides) -> RawExtraction:
        """Create a valid RawExtraction with optional overrides."""
        defaults = {
            "type": ExtractionType.TASK,
            "description": "Review the PR for MyProject",
            "confidence": 0.9,
            "project_ref": None,
            "deadline": None,
            "metadata": {},
        }
        defaults.update(overrides)
        return RawExtraction(**defaults)

    def make_valid_project_ref(self, **overrides) -> RawProjectRef:
        """Create a valid RawProjectRef with optional overrides."""
        defaults = {
            "name": "MyProject",
            "confidence": 0.85,
            "is_implicit": False,
        }
        defaults.update(overrides)
        return RawProjectRef(**defaults)

    def make_valid_deadline(self, **overrides) -> RawDeadline:
        """Create a valid RawDeadline with optional overrides."""
        defaults = {
            "kind": DeadlineKind.RELATIVE_DAY,
            "raw_text": "EOD",
            "confidence": 0.9,
        }
        defaults.update(overrides)
        return RawDeadline(**defaults)

    # ===== Valid extraction =====
    def test_valid_extraction_minimal(self):
        """A minimal valid extraction passes validation."""
        ext = self.make_valid_extraction()
        result = self.validator.validate(ext)
        assert result.is_valid is True
        assert result.error_count == 0

    def test_valid_extraction_with_project_ref(self):
        """Valid extraction with project reference passes."""
        ext = self.make_valid_extraction(project_ref=self.make_valid_project_ref())
        result = self.validator.validate(ext)
        assert result.is_valid is True

    def test_valid_extraction_with_deadline(self):
        """Valid extraction with deadline passes."""
        ext = self.make_valid_extraction(deadline=self.make_valid_deadline())
        result = self.validator.validate(ext)
        assert result.is_valid is True

    def test_valid_extraction_full(self):
        """Valid extraction with both project and deadline passes."""
        ext = self.make_valid_extraction(
            project_ref=self.make_valid_project_ref(),
            deadline=self.make_valid_deadline()
        )
        result = self.validator.validate(ext)
        assert result.is_valid is True

    # ===== Description validation =====
    def test_empty_description_fails(self):
        """Empty description is an ERROR."""
        ext = self.make_valid_extraction(description="")
        result = self.validator.validate(ext)
        assert result.is_valid is False
        assert result.error_count == 1
        assert result.errors[0].field == "description"
        assert "empty" in result.errors[0].message.lower()

    def test_whitespace_description_fails(self):
        """Whitespace-only description is an ERROR."""
        ext = self.make_valid_extraction(description="   \n\t  ")
        result = self.validator.validate(ext)
        assert result.is_valid is False
        assert result.error_count == 1
        assert result.errors[0].field == "description"

    def test_long_description_warns(self):
        """Over-length description is a WARNING, not an ERROR."""
        long_desc = "x" * 501
        ext = self.make_valid_extraction(description=long_desc)
        result = self.validator.validate(ext)
        assert result.is_valid is True  # Still valid
        assert result.warning_count == 1
        assert result.errors[0].field == "description"
        assert result.errors[0].severity == ValidationSeverity.WARNING

    # ===== Confidence validation =====
    def test_confidence_too_low_fails(self):
        """Confidence < 0.0 is an ERROR."""
        ext = self.make_valid_extraction(confidence=-0.1)
        result = self.validator.validate(ext)
        assert result.is_valid is False
        assert result.error_count == 1
        assert result.errors[0].field == "confidence"

    def test_confidence_too_high_fails(self):
        """Confidence > 1.0 is an ERROR."""
        ext = self.make_valid_extraction(confidence=1.1)
        result = self.validator.validate(ext)
        assert result.is_valid is False
        assert result.error_count == 1
        assert result.errors[0].field == "confidence"

    def test_confidence_boundary_values_pass(self):
        """Confidence exactly 0.0 and 1.0 pass."""
        for conf in [0.0, 1.0]:
            ext = self.make_valid_extraction(confidence=conf)
            result = self.validator.validate(ext)
            assert result.is_valid is True, f"Confidence {conf} should pass"

    def test_confidence_non_numeric_fails(self):
        """Non-numeric confidence is an ERROR."""
        ext = self.make_valid_extraction(confidence="high")
        result = self.validator.validate(ext)
        assert result.is_valid is False
        assert result.error_count == 1
        assert result.errors[0].field == "confidence"

    # ===== Project reference validation =====
    def test_empty_project_name_fails(self):
        """Empty project name is an ERROR."""
        ext = self.make_valid_extraction(project_ref=self.make_valid_project_ref(name=""))
        result = self.validator.validate(ext)
        assert result.is_valid is False
        assert result.error_count == 1
        assert result.errors[0].field == "project_ref.name"

    def test_whitespace_project_name_fails(self):
        """Whitespace-only project name is an ERROR."""
        ext = self.make_valid_extraction(project_ref=self.make_valid_project_ref(name="  "))
        result = self.validator.validate(ext)
        assert result.is_valid is False
        assert result.errors[0].field == "project_ref.name"

    def test_project_confidence_too_low_fails(self):
        """Project confidence < 0.0 is an ERROR."""
        ext = self.make_valid_extraction(project_ref=self.make_valid_project_ref(confidence=-0.1))
        result = self.validator.validate(ext)
        assert result.is_valid is False
        assert result.errors[0].field == "project_ref.confidence"

    def test_project_confidence_too_high_fails(self):
        """Project confidence > 1.0 is an ERROR."""
        ext = self.make_valid_extraction(project_ref=self.make_valid_project_ref(confidence=1.1))
        result = self.validator.validate(ext)
        assert result.is_valid is False
        assert result.errors[0].field == "project_ref.confidence"

    def test_project_is_implicit_non_bool_fails(self):
        """Non-boolean is_implicit is an ERROR."""
        ext = self.make_valid_extraction(project_ref=self.make_valid_project_ref(is_implicit="yes"))
        result = self.validator.validate(ext)
        assert result.is_valid is False
        assert result.errors[0].field == "project_ref.is_implicit"

    # ===== Deadline validation =====
    def test_empty_deadline_raw_text_fails(self):
        """Empty deadline raw_text is an ERROR."""
        ext = self.make_valid_extraction(deadline=self.make_valid_deadline(raw_text=""))
        result = self.validator.validate(ext)
        assert result.is_valid is False
        assert result.errors[0].field == "deadline.raw_text"

    def test_deadline_confidence_too_low_fails(self):
        """Deadline confidence < 0.0 is an ERROR."""
        ext = self.make_valid_extraction(deadline=self.make_valid_deadline(confidence=-0.1))
        result = self.validator.validate(ext)
        assert result.is_valid is False
        assert result.errors[0].field == "deadline.confidence"

    def test_deadline_confidence_too_high_fails(self):
        """Deadline confidence > 1.0 is an ERROR."""
        ext = self.make_valid_extraction(deadline=self.make_valid_deadline(confidence=1.1))
        result = self.validator.validate(ext)
        assert result.is_valid is False
        assert result.errors[0].field == "deadline.confidence"

    def test_invalid_deadline_kind_fails(self):
        """Invalid DeadlineKind is an ERROR."""
        # Create RawDeadline with invalid kind (bypassing type hints)
        deadline = RawDeadline(
            kind="not_a_kind",  # type: ignore
            raw_text="EOD",
            confidence=0.9
        )
        ext = self.make_valid_extraction(deadline=deadline)
        result = self.validator.validate(ext)
        assert result.is_valid is False
        assert result.errors[0].field == "deadline.kind"

    # ===== Multiple errors =====
    def test_multiple_errors_collected(self):
        """Multiple validation errors are all reported."""
        ext = RawExtraction(
            type=ExtractionType.TASK,
            description="",  # empty
            confidence=1.5,   # too high
            project_ref=RawProjectRef(name="", confidence=2.0, is_implicit=False),  # two errors
            deadline=RawDeadline(kind=DeadlineKind.RELATIVE_DAY, raw_text="", confidence=-0.5),  # two errors
        )
        result = self.validator.validate(ext)
        assert result.is_valid is False
        assert result.error_count == 6  # desc, conf, proj.name, proj.conf, dl.text, dl.conf
        # Actually: desc(1) + conf(1) + proj.name(1) + proj.conf(1) + dl.text(1) + dl.conf(1) = 6
        assert result.error_count >= 5

    # ===== Enum validation =====
    def test_invalid_extraction_type_fails(self):
        """Invalid ExtractionType is an ERROR."""
        ext = self.make_valid_extraction(type="not_a_type")  # type: ignore
        result = self.validator.validate(ext)
        assert result.is_valid is False
        assert result.errors[0].field == "type"

    # ===== ValidationResult utilities =====
    def test_validation_result_immutability(self):
        """ValidationResult is immutable (merge returns new instance)."""
        r1 = ValidationResult().add_error("field1", "error1")
        r2 = ValidationResult().add_error("field2", "error2")
        r3 = r1.merge(r2)

        assert len(r1.errors) == 1
        assert len(r2.errors) == 1
        assert len(r3.errors) == 2

    def test_validation_result_error_warning_counts(self):
        """Error and warning counts are correct."""
        result = (ValidationResult()
            .add_error("f1", "e1")
            .add_warning("f2", "w1")
            .add_error("f3", "e2"))
        assert result.error_count == 2
        assert result.warning_count == 1
        assert result.is_valid is False


class TestValidateExtractionConvenience:
    """Tests for the module-level validate_extraction function."""

    def test_convenience_function_works(self):
        """validate_extraction delegates to ExtractionValidator."""
        ext = RawExtraction(
            type=ExtractionType.TASK,
            description="Test task",
            confidence=0.8
        )
        result = validate_extraction(ext)
        assert result.is_valid is True

    def test_convenience_function_finds_errors(self):
        """Convenience function returns errors correctly."""
        ext = RawExtraction(
            type=ExtractionType.TASK,
            description="",
            confidence=0.8
        )
        result = validate_extraction(ext)
        assert result.is_valid is False
        assert result.error_count == 1