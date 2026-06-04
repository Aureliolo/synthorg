"""Domain error hierarchy for the self-extending toolkit (toolsmith).

Follows the project convention of subclassing
:class:`synthorg.core.domain_errors.DomainError` so error names carry
intent. Toolsmith errors surface to the operator / agent driving tool
creation, so they fall under :class:`ErrorCategory.INTERNAL`.
"""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class ToolsmithError(DomainError):
    """Base for every error raised by the self-extending toolkit."""

    default_message: ClassVar[str] = "Toolsmith operation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500


class ToolAuthoringError(ToolsmithError):
    """Raised when model output cannot be parsed into a valid blueprint."""

    default_message: ClassVar[str] = "Authored tool blueprint failed validation"


class ToolCapabilityNotAllowedError(ToolsmithError):
    """Raised when a blueprint targets a capability outside the allowlist."""

    default_message: ClassVar[str] = "Capability is not in the toolsmith allowlist"


class ToolValidationFailedError(ToolsmithError):
    """Raised when the benchmark gate rejects a candidate blueprint."""

    default_message: ClassVar[str] = "Authored tool failed the benchmark gate"


class ToolRegistrationError(ToolsmithError):
    """Raised when a validated blueprint cannot be live-registered."""

    default_message: ClassVar[str] = "Authored tool could not be registered"


class UnknownGoldenScorecardProviderError(ToolsmithError):
    """Raised when the golden-scorecard discriminator names an unknown arm."""

    default_message: ClassVar[str] = "Unknown golden-scorecard provider strategy"


class GoldenScorecardUnavailableError(ToolsmithError):
    """Raised when the eval-backed golden scorecard cannot locate the harness."""

    default_message: ClassVar[str] = "Golden-company eval harness is unavailable"


__all__ = [
    "GoldenScorecardUnavailableError",
    "ToolAuthoringError",
    "ToolCapabilityNotAllowedError",
    "ToolRegistrationError",
    "ToolValidationFailedError",
    "ToolsmithError",
    "UnknownGoldenScorecardProviderError",
]
