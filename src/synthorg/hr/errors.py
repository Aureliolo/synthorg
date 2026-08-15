"""HR domain error hierarchy.

All HR errors default to ``is_retryable = False``. HR operations are
either deterministic lookups (agent registry, personality catalogue,
training session store) or write-ops against authoritative state
(hiring, promotion, pruning) where silent retries would double-apply.
Subclasses that genuinely represent a transient network/I/O failure
should override ``is_retryable = True`` explicitly.
"""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class HRError(DomainError):
    """Base error for all HR operations.

    ``is_retryable`` defaults to ``False`` so the provider-retry layer
    surfaces HR errors immediately; subclasses override to ``True``
    only for genuine transient I/O / network failures.
    """

    is_retryable: bool = False
    default_message: ClassVar[str] = "HR operation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500


# ── Hiring ────────────────────────────────────────────────────────


class HiringError(HRError):
    """Error during the hiring process."""


class HiringApprovalRequiredError(HiringError):
    """Hiring request requires approval before instantiation."""

    default_message: ClassVar[str] = "Hiring request requires approval"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.HIRING_APPROVAL_REQUIRED
    status_code: ClassVar[int] = 409


class HiringAlreadyInFlightError(HiringError):
    """A hire for this role is already on its way to an agent.

    Raised only for the roles where a second request answers nothing the
    first does not: the gate roles are held org-wide rather than per team,
    so a duplicate is one more approval item asking the operator the same
    question. Ordinary headcount is not deduplicated here, because two
    teams wanting the same role is two hires.
    """

    default_message: ClassVar[str] = "A hire for this role is already in flight"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.HIRING_ALREADY_IN_FLIGHT
    status_code: ClassVar[int] = 409


class HiringRejectedError(HiringError):
    """Hiring request was rejected."""

    default_message: ClassVar[str] = "Hiring request was rejected"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.HIRING_REJECTED
    status_code: ClassVar[int] = 409


class InvalidCandidateError(HiringError):
    """Candidate card is invalid or does not exist on the request."""

    default_message: ClassVar[str] = "Invalid candidate card"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    status_code: ClassVar[int] = 422


# ── Firing / Offboarding ─────────────────────────────────────────


class FiringError(HRError):
    """Error during the firing process."""


class OffboardingError(HRError):
    """Error during the offboarding pipeline."""


class TaskReassignmentError(OffboardingError):
    """Failed to reassign tasks from a departing agent."""


class MemoryArchivalError(OffboardingError):
    """Failed to archive agent memories during offboarding."""


# ── Onboarding ───────────────────────────────────────────────────


class OnboardingError(HRError):
    """Error during the onboarding process."""


# ── Agent Registry ───────────────────────────────────────────────


class AgentRegistryError(HRError):
    """Error in the agent registry."""


class AgentNotFoundError(AgentRegistryError):
    """Agent not found in the registry."""

    default_message: ClassVar[str] = "Agent not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.AGENT_NOT_FOUND
    status_code: ClassVar[int] = 404


class AgentAlreadyRegisteredError(AgentRegistryError):
    """Agent is already registered."""

    default_message: ClassVar[str] = "Agent already registered"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.AGENT_ALREADY_REGISTERED
    status_code: ClassVar[int] = 409


# ── Performance ──────────────────────────────────────────────────


class PerformanceError(HRError):
    """Error in the performance tracking system."""


class InsufficientDataError(PerformanceError):
    """Not enough data points for a meaningful computation."""


# ── Pruning ────────────────────────────────────────────────────


class PruningError(HRError):
    """Error during the pruning process."""


class PruningUnrestartableError(PruningError):
    """Raised when ``PruningService.start()`` is called after a timed-out stop.

    Mirrors :class:`BackupUnrestartableError`: a stuck drain leaves an
    orphan loop that may still hold references the new instance would
    race; the canonical lifecycle pattern marks the service unrestartable
    and forces operators to construct a fresh one.
    """

    default_message: ClassVar[str] = (
        "Pruning service is unrestartable after a timed-out stop"
    )
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.PRUNING_UNRESTARTABLE
    status_code: ClassVar[int] = 409


# ── Personalities ───────────────────────────────────────────────


class PersonalityError(HRError):
    """Error in the personality preset catalogue."""


class PersonalityNotFoundError(PersonalityError):
    """Personality preset not found in the catalogue."""

    default_message: ClassVar[str] = "Personality preset not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.PERSONALITY_NOT_FOUND
    status_code: ClassVar[int] = 404


# ── Training ────────────────────────────────────────────────────


class TrainingError(HRError):
    """Error in the training pipeline."""


class TrainingSessionNotFoundError(TrainingError):
    """Training session not found in the session store."""

    default_message: ClassVar[str] = "Training session not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.TRAINING_SESSION_NOT_FOUND
    status_code: ClassVar[int] = 404
