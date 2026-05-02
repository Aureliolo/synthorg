"""HR domain error hierarchy.

All HR errors default to ``is_retryable = False``. HR operations are
either deterministic lookups (agent registry, personality catalogue,
training session store) or write-ops against authoritative state
(hiring, promotion, pruning) where silent retries would double-apply.
Subclasses that genuinely represent a transient network/I/O failure
should override ``is_retryable = True`` explicitly.
"""

from typing import ClassVar

from synthorg.core.domain_errors import ConflictError


class HRError(Exception):
    """Base error for all HR operations.

    ``is_retryable`` defaults to ``False`` so the provider-retry layer
    surfaces HR errors immediately; subclasses override to ``True``
    only for genuine transient I/O / network failures.
    """

    is_retryable: bool = False


# ── Hiring ────────────────────────────────────────────────────────


class HiringError(HRError):
    """Error during the hiring process."""


class HiringApprovalRequiredError(HiringError):
    """Hiring request requires approval before instantiation."""


class HiringRejectedError(HiringError):
    """Hiring request was rejected."""


class InvalidCandidateError(HiringError):
    """Candidate card is invalid or does not exist on the request."""


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


class AgentAlreadyRegisteredError(AgentRegistryError):
    """Agent is already registered."""


# ── Performance ──────────────────────────────────────────────────


class PerformanceError(HRError):
    """Error in the performance tracking system."""


class InsufficientDataError(PerformanceError):
    """Not enough data points for a meaningful computation."""


# ── Promotion ───────────────────────────────────────────────────


class PromotionError(HRError):
    """Error during the promotion/demotion process."""


class PromotionCooldownError(PromotionError):
    """Promotion is blocked by the cooldown period."""


class PromotionApprovalRequiredError(PromotionError):
    """Promotion requires human approval before proceeding."""


# ── Pruning ────────────────────────────────────────────────────


class PruningError(HRError):
    """Error during the pruning process."""


class PruningUnrestartableError(ConflictError):
    """Raised when ``PruningService.start()`` is called after a timed-out stop.

    Mirrors :class:`BackupUnrestartableError`: a stuck drain leaves an
    orphan loop that may still hold references the new instance would
    race; the canonical lifecycle pattern marks the service unrestartable
    and forces operators to construct a fresh one.
    """

    default_message: ClassVar[str] = (
        "Pruning service is unrestartable after a timed-out stop"
    )


# ── Personalities ───────────────────────────────────────────────


class PersonalityError(HRError):
    """Error in the personality preset catalogue."""


class PersonalityNotFoundError(PersonalityError):
    """Personality preset not found in the catalogue."""


# ── Training ────────────────────────────────────────────────────


class TrainingError(HRError):
    """Error in the training pipeline."""


class TrainingSessionNotFoundError(TrainingError):
    """Training session not found in the session store."""
