"""Domain enumerations for the AI company framework."""

from enum import StrEnum


class SeniorityLevel(StrEnum):
    """Seniority levels for agents within the organization.

    Each level corresponds to an authority scope, typical model tier, and
    cost tier defined in ``ai_company.core.role_catalog.SENIORITY_INFO``.
    """

    # DESIGN_SPEC §3.2 lists "Intern/Junior" — collapsed to JUNIOR (approved deviation).
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    PRINCIPAL = "principal"
    DIRECTOR = "director"
    VP = "vp"
    C_SUITE = "c_suite"


_SENIORITY_ORDER: tuple[SeniorityLevel, ...] = tuple(SeniorityLevel)

# Validate that _SENIORITY_ORDER contains every SeniorityLevel member
# exactly once and preserves enum declaration order.  This guards
# against silent breakage if the enum is reordered or extended without
# updating the ordering tuple.
_all_members = set(SeniorityLevel)
_order_set = set(_SENIORITY_ORDER)
if _order_set != _all_members:
    _missing = _all_members - _order_set
    _extra = _order_set - _all_members
    _msg = (
        f"_SENIORITY_ORDER is out of sync with SeniorityLevel: "
        f"missing={_missing}, extra={_extra}"
    )
    raise RuntimeError(_msg)
if len(_SENIORITY_ORDER) != len(_order_set):
    _msg = "_SENIORITY_ORDER contains duplicate entries"
    raise RuntimeError(_msg)
del _all_members, _order_set

# Precomputed rank lookup for O(1) seniority comparison.
_SENIORITY_RANK: dict[SeniorityLevel, int] = {
    level: idx for idx, level in enumerate(_SENIORITY_ORDER)
}


def compare_seniority(a: SeniorityLevel, b: SeniorityLevel) -> int:
    """Compare two seniority levels.

    Returns negative if *a* is junior to *b*, zero if equal,
    positive if *a* is senior to *b*.

    Args:
        a: First seniority level.
        b: Second seniority level.

    Returns:
        Integer indicating relative seniority.
    """
    return _SENIORITY_RANK[a] - _SENIORITY_RANK[b]


class AgentStatus(StrEnum):
    """Lifecycle status of an agent."""

    ACTIVE = "active"
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


class MemoryType(StrEnum):
    """Memory persistence type for an agent."""

    PERSISTENT = "persistent"
    PROJECT = "project"
    SESSION = "session"
    NONE = "none"


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


class CompanyType(StrEnum):
    """Pre-defined company template types."""

    SOLO_FOUNDER = "solo_founder"
    STARTUP = "startup"
    DEV_SHOP = "dev_shop"
    PRODUCT_TEAM = "product_team"
    AGENCY = "agency"
    FULL_COMPANY = "full_company"
    RESEARCH_LAB = "research_lab"
    CUSTOM = "custom"


class SkillCategory(StrEnum):
    """Categories for agent skills."""

    ENGINEERING = "engineering"
    PRODUCT = "product"
    DESIGN = "design"
    DATA = "data"
    QA = "qa"
    OPERATIONS = "operations"
    SECURITY = "security"
    CREATIVE = "creative"
    MANAGEMENT = "management"


class ProficiencyLevel(StrEnum):
    """Proficiency level for a skill."""

    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class DepartmentName(StrEnum):
    """Standard department names within the organization."""

    EXECUTIVE = "executive"
    PRODUCT = "product"
    DESIGN = "design"
    ENGINEERING = "engineering"
    QUALITY_ASSURANCE = "quality_assurance"
    DATA_ANALYTICS = "data_analytics"
    OPERATIONS = "operations"
    CREATIVE_MARKETING = "creative_marketing"
    SECURITY = "security"


class TaskStatus(StrEnum):
    """Lifecycle status of a task.

    The authoritative transition map lives in
    ``ai_company.core.task_transitions.VALID_TRANSITIONS``.
    Summary for quick reference:

        CREATED -> ASSIGNED
        ASSIGNED -> IN_PROGRESS | BLOCKED | CANCELLED | FAILED | INTERRUPTED
        IN_PROGRESS -> IN_REVIEW | BLOCKED | CANCELLED | FAILED | INTERRUPTED
        IN_REVIEW -> COMPLETED | IN_PROGRESS (rework) | BLOCKED | CANCELLED
        BLOCKED -> ASSIGNED (unblocked)
        FAILED -> ASSIGNED (reassignment for retry)
        INTERRUPTED -> ASSIGNED (reassignment on restart)
        COMPLETED and CANCELLED are terminal states.
        FAILED and INTERRUPTED are non-terminal (can be reassigned).
    """

    CREATED = "created"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class TaskType(StrEnum):
    """Classification of the kind of work a task represents."""

    DEVELOPMENT = "development"
    DESIGN = "design"
    RESEARCH = "research"
    REVIEW = "review"
    MEETING = "meeting"
    ADMIN = "admin"


class Priority(StrEnum):
    """Task urgency and importance level."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Complexity(StrEnum):
    """Estimated task complexity."""

    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    EPIC = "epic"


class ArtifactType(StrEnum):
    """Type of produced artifact."""

    CODE = "code"
    TESTS = "tests"
    DOCUMENTATION = "documentation"


class ProjectStatus(StrEnum):
    """Lifecycle status of a project."""

    PLANNING = "planning"
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ToolAccessLevel(StrEnum):
    """Access level for tool permissions.

    Determines which tool categories an agent can use.
    Levels ``SANDBOXED`` through ``ELEVATED`` form a hierarchy
    where each includes all categories from lower levels.
    ``CUSTOM`` uses only explicit allow/deny lists, ignoring
    the hierarchy.

    The concrete category sets for each level are defined in
    ``ToolPermissionChecker._LEVEL_CATEGORIES``.
    """

    SANDBOXED = "sandboxed"
    RESTRICTED = "restricted"
    STANDARD = "standard"
    ELEVATED = "elevated"
    CUSTOM = "custom"


class ToolCategory(StrEnum):
    """Category of a tool for access-level gating."""

    FILE_SYSTEM = "file_system"
    CODE_EXECUTION = "code_execution"
    VERSION_CONTROL = "version_control"
    WEB = "web"
    DATABASE = "database"
    TERMINAL = "terminal"
    DESIGN = "design"
    COMMUNICATION = "communication"
    ANALYTICS = "analytics"
    DEPLOYMENT = "deployment"
    MCP = "mcp"
    OTHER = "other"


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


class TaskStructure(StrEnum):
    """Classification of how a task's subtasks relate to each other.

    Used by the decomposition engine to determine coordination topology
    and execution ordering. See DESIGN_SPEC Section 6.9.
    """

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    MIXED = "mixed"


class CoordinationTopology(StrEnum):
    """Coordination topology for multi-agent task execution.

    Determines how agents coordinate when executing decomposed subtasks.
    See DESIGN_SPEC Section 6.9.
    """

    SAS = "sas"
    CENTRALIZED = "centralized"
    DECENTRALIZED = "decentralized"
    CONTEXT_DEPENDENT = "context_dependent"
    AUTO = "auto"


class ActionType(StrEnum):
    """Convenience constants for common approval action types.

    Models typically use ``NotBlankStr`` for ``action_type`` fields, so these
    are optional helper constants and custom string values remain valid.
    """

    CODE_MERGE = "code_merge"
    DEPLOYMENT = "deployment"
    BUDGET_SPEND = "budget_spend"
    EXTERNAL_COMMUNICATION = "external_communication"
    HIRING = "hiring"
    ARCHITECTURE_CHANGE = "architecture_change"
