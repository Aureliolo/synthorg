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
    LEARNED_FROM_SENIORS = "learned_from_seniors"


class LifecycleEventType(StrEnum):
    """Type of agent lifecycle event."""

    HIRED = "hired"
    ONBOARDED = "onboarded"
    FIRED = "fired"
    OFFBOARDED = "offboarded"
    STATUS_CHANGED = "status_changed"
    PROMOTED = "promoted"
    DEMOTED = "demoted"


class ActivityEventType(StrEnum):
    """Event types produced by the activity feed timeline.

    Superset of ``LifecycleEventType`` plus operational event types
    generated from task metrics, cost records, tool invocations,
    and delegation records.
    """

    HIRED = "hired"
    ONBOARDED = "onboarded"
    FIRED = "fired"
    OFFBOARDED = "offboarded"
    STATUS_CHANGED = "status_changed"
    PROMOTED = "promoted"
    DEMOTED = "demoted"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    COST_INCURRED = "cost_incurred"
    TOOL_USED = "tool_used"
    DELEGATION_SENT = "delegation_sent"
    DELEGATION_RECEIVED = "delegation_received"


# Import-time check: ActivityEventType must be a superset of LifecycleEventType.
_lifecycle_values = {e.value for e in LifecycleEventType}
_activity_values = {e.value for e in ActivityEventType}
assert _lifecycle_values <= _activity_values, (  # noqa: S101
    "ActivityEventType must be superset of LifecycleEventType; "
    f"missing: {_lifecycle_values - _activity_values}"
)


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


class AgentStatus(StrEnum):
    """Lifecycle status of an agent."""

    ACTIVE = "active"
    ONBOARDING = "onboarding"
    ON_LEAVE = "on_leave"
    TERMINATED = "terminated"


class RiskTolerance(StrEnum):
    """Risk tolerance level for agent personality."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CreativityLevel(StrEnum):
    """Creativity level for agent personality."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DecisionMakingStyle(StrEnum):
    """Decision-making approach used by an agent."""

    ANALYTICAL = "analytical"
    INTUITIVE = "intuitive"
    CONSULTATIVE = "consultative"
    DIRECTIVE = "directive"


class CollaborationPreference(StrEnum):
    """Preferred collaboration mode for an agent."""

    INDEPENDENT = "independent"
    PAIR = "pair"
    TEAM = "team"


class CommunicationVerbosity(StrEnum):
    """Communication verbosity level for an agent."""

    TERSE = "terse"
    BALANCED = "balanced"
    VERBOSE = "verbose"


class ConflictApproach(StrEnum):
    """Conflict resolution approach used by an agent."""

    AVOID = "avoid"
    ACCOMMODATE = "accommodate"
    COMPETE = "compete"
    COMPROMISE = "compromise"
    COLLABORATE = "collaborate"


class CostTier(StrEnum):
    """Built-in cost tier identifiers.

    These are the default tiers shipped with the framework. Users can
    define additional tiers via configuration. Fields that accept cost
    tiers (e.g. ``SeniorityInfo.cost_tier``) use ``str`` rather than
    this enum, so custom tier IDs are also valid.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    PREMIUM = "premium"
