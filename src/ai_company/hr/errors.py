"""HR domain error hierarchy."""


class HRError(Exception):
    """Base error for all HR operations."""


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
