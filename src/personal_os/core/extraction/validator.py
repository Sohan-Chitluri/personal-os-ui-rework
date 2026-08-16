"""Validation layer for extraction pipeline.

The Validator checks structure and invariants of RawExtraction objects.
It performs NO semantic interpretation, policy decisions, or external calls.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional
from personal_os.core.extraction.models import (
    RawExtraction,
    RawProjectRef,
    RawDeadline,
    ExtractionType,
    DeadlineKind,
)


class ValidationSeverity(Enum):
    """Severity of a validation issue."""
    ERROR = "error"      # Extraction is invalid, should not proceed
    WARNING = "warning"  # Extraction is valid but has potential issues


@dataclass(frozen=True, slots=True)
class ValidationError:
    """A single validation issue found in a RawExtraction.

    Attributes:
        field: Dot-path to the problematic field (e.g., "description", "project_ref.confidence").
        message: Human-readable description of the issue.
        severity: ERROR means the extraction should be rejected; WARNING means it can proceed.
    """
    field: str
    message: str
    severity: ValidationSeverity


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Result of validating a RawExtraction.

    Attributes:
        is_valid: True if no ERROR-severity issues were found.
        errors: All validation issues found (both ERROR and WARNING).
    """
    errors: List[ValidationError] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not any(e.severity == ValidationSeverity.ERROR for e in self.errors)

    @property
    def error_count(self) -> int:
        return sum(1 for e in self.errors if e.severity == ValidationSeverity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for e in self.errors if e.severity == ValidationSeverity.WARNING)

    def add_error(self, field: str, message: str) -> ValidationResult:
        """Return new result with an ERROR added (immutable)."""
        return ValidationResult(errors=self.errors + [
            ValidationError(field=field, message=message, severity=ValidationSeverity.ERROR)
        ])

    def add_warning(self, field: str, message: str) -> ValidationResult:
        """Return new result with a WARNING added (immutable)."""
        return ValidationResult(errors=self.errors + [
            ValidationError(field=field, message=message, severity=ValidationSeverity.WARNING)
        ])

    def merge(self, other: ValidationResult) -> ValidationResult:
        """Combine with another result."""
        return ValidationResult(errors=self.errors + other.errors)


class ExtractionValidator:
    """Validates RawExtraction structure and invariants.

    Purely structural validation — no semantic interpretation, no vault access,
    no LLM calls, no policy decisions.
    """

    # Structural limits
    MAX_DESCRIPTION_LENGTH = 500
    MIN_CONFIDENCE = 0.0
    MAX_CONFIDENCE = 1.0

    def validate(self, extraction: RawExtraction) -> ValidationResult:
        """Validate a RawExtraction and return all issues found."""
        result = ValidationResult()

        # Root-level required fields
        result = self._validate_root_fields(extraction, result)

        # Confidence bounds
        result = self._validate_confidence(extraction.confidence, "confidence", result)

        # Project reference (if present)
        if extraction.project_ref is not None:
            result = self._validate_project_ref(extraction.project_ref, result)

        # Deadline (if present)
        if extraction.deadline is not None:
            result = self._validate_deadline(extraction.deadline, result)

        # Enum validity (redundant with type hints but catches runtime issues)
        result = self._validate_enums(extraction, result)

        return result

    def _validate_root_fields(self, extraction: RawExtraction, result: ValidationResult) -> ValidationResult:
        """Validate required root fields."""
        if not extraction.description or not extraction.description.strip():
            result = result.add_error(
                "description",
                "Description is required and cannot be empty or whitespace"
            )
        elif len(extraction.description) > self.MAX_DESCRIPTION_LENGTH:
            result = result.add_warning(
                "description",
                f"Description exceeds {self.MAX_DESCRIPTION_LENGTH} characters, may be truncated in storage"
            )
        return result

    def _validate_confidence(self, confidence: float, field_prefix: str, result: ValidationResult) -> ValidationResult:
        """Validate a confidence value is within bounds."""
        if not isinstance(confidence, (int, float)):
            result = result.add_error(
                field_prefix,
                f"Confidence must be a number, got {type(confidence).__name__}"
            )
        elif confidence < self.MIN_CONFIDENCE or confidence > self.MAX_CONFIDENCE:
            result = result.add_error(
                field_prefix,
                f"Confidence {confidence} out of bounds [{self.MIN_CONFIDENCE}, {self.MAX_CONFIDENCE}]"
            )
        return result

    def _validate_project_ref(self, project_ref: RawProjectRef, result: ValidationResult) -> ValidationResult:
        """Validate RawProjectRef structure."""
        if not project_ref.name or not project_ref.name.strip():
            result = result.add_error(
                "project_ref.name",
                "Project reference name is required and cannot be empty"
            )

        result = self._validate_confidence(
            project_ref.confidence,
            "project_ref.confidence",
            result
        )

        if not isinstance(project_ref.is_implicit, bool):
            result = result.add_error(
                "project_ref.is_implicit",
                f"is_implicit must be boolean, got {type(project_ref.is_implicit).__name__}"
            )
        return result

    def _validate_deadline(self, deadline: RawDeadline, result: ValidationResult) -> ValidationResult:
        """Validate RawDeadline structure."""
        if not deadline.raw_text or not deadline.raw_text.strip():
            result = result.add_error(
                "deadline.raw_text",
                "Deadline raw_text is required and cannot be empty"
            )

        if not isinstance(deadline.kind, DeadlineKind):
            result = result.add_error(
                "deadline.kind",
                f"Invalid DeadlineKind: {deadline.kind}"
            )

        result = self._validate_confidence(
            deadline.confidence,
            "deadline.confidence",
            result
        )
        return result

    def _validate_enums(self, extraction: RawExtraction, result: ValidationResult) -> ValidationResult:
        """Validate enum fields are valid enum members."""
        if not isinstance(extraction.type, ExtractionType):
            result = result.add_error(
                "type",
                f"Invalid ExtractionType: {extraction.type}"
            )
        return result


# Convenience function for simple use cases
def validate_extraction(extraction: RawExtraction) -> ValidationResult:
    """Validate a single extraction (module-level convenience)."""
    return ExtractionValidator().validate(extraction)