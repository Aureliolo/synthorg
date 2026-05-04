"""Domain errors for the self-improving meta-loop.

Errors here are raised by the service layer and translated to MCP /
REST envelopes by the handler layer. They carry enough context for
operators to disambiguate why a cycle could not run without leaking
internal config state.
"""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class SelfImprovementError(DomainError):
    """Base class for self-improvement service domain errors."""

    default_message: ClassVar[str] = "Self-improvement operation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500


class SelfImprovementTriggerError(SelfImprovementError):
    """Raised when ``SelfImprovementService.trigger_cycle`` cannot run.

    Triggers fail when prerequisites are missing -- for example, no
    snapshot builder is wired -- rather than running with degraded
    inputs that would produce misleading proposals.
    """
