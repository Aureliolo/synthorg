"""HR domain enumerations."""

from enum import StrEnum


class HiringRequestStatus(StrEnum):
    """Status of a hiring request through the approval pipeline."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    INSTANTIATED = "instantiated"


class FiringReason(StrEnum):
    """Reason for agent termination."""

    MANUAL = "manual"
    PERFORMANCE = "performance"
    BUDGET = "budget"
    PROJECT_COMPLETION = "project_completion"


class OnboardingStep(StrEnum):
    """Steps in the agent onboarding checklist."""

    COMPANY_CONTEXT = "company_context"
    PROJECT_BRIEFING = "project_briefing"
    TEAM_INTRODUCTIONS = "team_introductions"


class LifecycleEventType(StrEnum):
    """Type of agent lifecycle event."""

    HIRED = "hired"
    ONBOARDED = "onboarded"
    FIRED = "fired"
    OFFBOARDED = "offboarded"
    STATUS_CHANGED = "status_changed"
    PROMOTED = "promoted"
    DEMOTED = "demoted"


class PromotionDirection(StrEnum):
    """Direction of a seniority level change."""

    PROMOTION = "promotion"
    DEMOTION = "demotion"


class TrendDirection(StrEnum):
    """Direction of a performance metric trend."""

    IMPROVING = "improving"
    STABLE = "stable"
    DECLINING = "declining"
    INSUFFICIENT_DATA = "insufficient_data"
