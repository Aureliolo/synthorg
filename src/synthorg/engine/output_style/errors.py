# module-kind: declarative
"""Typed domain errors for the output-style policy subsystem.

Every error inherits :class:`~synthorg.core.domain_errors.DomainError` so the
API exception handler maps it to an RFC 9457 response. A hard-rule violation is
a validation failure at an output boundary (422); the agent that produced the
output is told the specific rule so it can rework.
"""

from typing import ClassVar

from synthorg.core.domain_errors import NotFoundError, ValidationError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class OutputStyleError(ValidationError):
    """Base for output-style policy validation failures."""

    default_message: ClassVar[str] = "Output-style policy validation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.OUTPUT_STYLE_VIOLATION
    status_code: ClassVar[int] = 422


class OutputPolicyViolationError(OutputStyleError):
    """Raised when agent output violates a hard rule and is rejected.

    The message is the verdict's aggregated summary (the distinct blocking-rule
    reasons), so the producing agent sees which rules it violated and can
    rework; the structured verdict itself is not attached to the error.
    """

    default_message: ClassVar[str] = "Output violates a hard output-style rule"
    error_code: ClassVar[ErrorCode] = ErrorCode.OUTPUT_STYLE_VIOLATION


class OutputStylePackValidationError(OutputStyleError):
    """Raised when an output-style rule pack fails schema validation."""

    default_message: ClassVar[str] = "Output-style pack validation failed"
    error_code: ClassVar[ErrorCode] = ErrorCode.OUTPUT_STYLE_PACK_INVALID


class OutputStyleExemptionError(OutputStyleError):
    """Raised when a sanctioned-exemption definition is malformed."""

    default_message: ClassVar[str] = "Output-style exemption is invalid"
    error_code: ClassVar[ErrorCode] = ErrorCode.OUTPUT_STYLE_EXEMPTION_INVALID


class OutputStylePackNotFoundError(NotFoundError):
    """Raised when a requested output-style rule pack cannot be found."""

    default_message: ClassVar[str] = "Output-style pack not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.OUTPUT_STYLE_PACK_NOT_FOUND
    status_code: ClassVar[int] = 404


__all__ = [
    "OutputPolicyViolationError",
    "OutputStyleError",
    "OutputStyleExemptionError",
    "OutputStylePackNotFoundError",
    "OutputStylePackValidationError",
]
