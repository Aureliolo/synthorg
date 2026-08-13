# module-kind: code
"""Errors for stakes-aware model routing."""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.task_enums import Stakes
from synthorg.core.types import CapabilityLevel


class StakesModelUnavailableError(DomainError):
    """No configured model clears a task's stakes capability floor (503).

    Raised by the stakes-aware strategy when no configured, tool-capable
    model sits at or above the capability the task's stakes demand. The engine
    escalates this (parks for an operator decision when an approval gate is
    wired) or fails the task loudly, so consequential work is never silently
    run on an under-capable model.

    Attributes:
        stakes: The stakes level whose capability floor could not be cleared.
        required_capability: The minimum capability that was required.
    """

    default_message: ClassVar[str] = (
        "No configured tool-capable model meets the required capability for "
        "this task's stakes. Add or enable a stronger provider/model, or "
        "lower the task's stakes."
    )
    error_category: ClassVar[ErrorCategory] = ErrorCategory.PROVIDER_ERROR
    error_code: ClassVar[ErrorCode] = ErrorCode.STAKES_MODEL_UNAVAILABLE
    status_code: ClassVar[int] = 503

    stakes: Stakes
    required_capability: CapabilityLevel

    def __init__(
        self,
        message: str | None = None,
        *,
        stakes: Stakes,
        required_capability: CapabilityLevel,
    ) -> None:
        super().__init__(message)
        self.stakes = stakes
        self.required_capability = required_capability


__all__ = ["StakesModelUnavailableError"]
