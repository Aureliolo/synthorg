"""Domain error hierarchy for the vision verifier subsystem.

Every failure path raises a ``<Vision><Condition>Error`` subclass of
:class:`synthorg.core.domain_errors.DomainError` so the
``check_domain_error_hierarchy.py`` gate stays clean.
"""

import copy
from types import MappingProxyType
from typing import Any, ClassVar, override

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class VisionDomainError(DomainError):
    """Base for all vision-verifier domain errors.

    Carries an immutable ``context`` mapping for structured metadata, so
    callers can attach the offending screenshot path / model id without
    smuggling it into the message.
    """

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Vision verifier failure"

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Initialise with a message and an optional metadata mapping."""
        self.message = message
        self.context: MappingProxyType[str, Any] = MappingProxyType(
            copy.deepcopy(context) if context else {},
        )
        super().__init__(message)

    @override
    def __str__(self) -> str:
        """Format the error with optional context metadata."""
        if self.context:
            ctx = ", ".join(f"{k}={v!r}" for k, v in self.context.items())
            return f"{self.message} ({ctx})"
        return self.message


class VisionVerifyConfigError(VisionDomainError):
    """A vision verifier could not be built from its configuration.

    Raised at construction (fail fast) when a selected verifier kind is
    missing a required dependency (e.g. ``llm_vision`` without a
    provider).
    """

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Vision verifier configuration invalid"


class VisionModelUnsupportedError(VisionDomainError):
    """The configured model does not accept image inputs.

    Raised by the ``llm_vision`` verifier when the resolved model's
    ``ModelCapabilities.supports_vision`` is ``False``: sending images
    to a text-only model would silently drop them.
    """

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Configured model does not support vision"


class VisionScreenshotError(VisionDomainError):
    """A referenced screenshot could not be read or decoded."""

    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Vision verifier could not read a screenshot"
