# module-kind: code
"""Errors for capability-based agent selection."""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.task_enums import Stakes
from synthorg.core.types import CapabilityLevel


class StakesModelUnavailableError(DomainError):
    """No agent clears a task's stakes capability requirement (503).

    Raised when the work's stakes sit at or above the configured park floor
    and no agent runs at or above the rung it demands. Below that floor the
    nearest weaker agent takes the work and the concession is logged; at or
    above it the engine escalates (parks for an operator decision when an
    approval gate is wired) or fails the task loudly, so consequential work is
    never silently run under-capable.

    Attributes:
        stakes: The stakes level whose capability floor could not be cleared.
        required_capability: The minimum capability that was required.
        unresolved: Whether the agent's bound pair carries no rung at all,
            rather than one below the requirement. The two refusals ask
            different things of the operator, and the remedy for a weak pair
            (bind a stronger model) does nothing for a pair that was never
            graded, so the distinction has to survive as far as the message.
    """

    default_message: ClassVar[str] = (
        "No available agent runs a model at the capability this task's stakes "
        "require. Staff an agent bound to a stronger model, or lower the "
        "task's stakes."
    )
    error_category: ClassVar[ErrorCategory] = ErrorCategory.PROVIDER_ERROR
    error_code: ClassVar[ErrorCode] = ErrorCode.STAKES_MODEL_UNAVAILABLE
    status_code: ClassVar[int] = 503

    stakes: Stakes
    required_capability: CapabilityLevel
    unresolved: bool

    def __init__(
        self,
        message: str | None = None,
        *,
        stakes: Stakes,
        required_capability: CapabilityLevel,
        unresolved: bool = False,
    ) -> None:
        super().__init__(message)
        self.stakes = stakes
        self.required_capability = required_capability
        self.unresolved = unresolved


__all__ = ["StakesModelUnavailableError"]
