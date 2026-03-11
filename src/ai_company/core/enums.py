"""Domain enumerations for the SynthOrg framework."""

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


class MemoryLevel(StrEnum):
    """Memory persistence level for an agent (§7.3)."""

    PERSISTENT = "persistent"
    PROJECT = "project"
    SESSION = "session"
    NONE = "none"


class MemoryCategory(StrEnum):
    """Memory type categories for agent memory (§7.2)."""

    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    SOCIAL = "social"


class ConsolidationInterval(StrEnum):
    """Interval for memory consolidation."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    NEVER = "never"


class OrgFactCategory(StrEnum):
    """Category of organizational fact (§7.4).

    Categorizes shared organizational knowledge entries by their nature
    and purpose within the company.
    """

    CORE_POLICY = "core_policy"
    ADR = "adr"
    PROCEDURE = "procedure"
    CONVENTION = "convention"


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
    """Two-level action type taxonomy for security classification.

    Used by autonomy presets (DESIGN_SPEC §12.2), SecOps validation
    (§12.3), tiered timeout policies (§12.4), and progressive trust
    (§11.3).  Values follow a ``category:action`` naming convention.

    Custom action type strings are also accepted by models that use
    ``str`` for ``action_type`` fields — these enum members are
    convenience constants for the built-in taxonomy.
    """

    CODE_READ = "code:read"
    CODE_WRITE = "code:write"
    CODE_CREATE = "code:create"
    CODE_DELETE = "code:delete"
    CODE_REFACTOR = "code:refactor"
    TEST_WRITE = "test:write"
    TEST_RUN = "test:run"
    DOCS_WRITE = "docs:write"
    VCS_COMMIT = "vcs:commit"
    VCS_PUSH = "vcs:push"
    VCS_BRANCH = "vcs:branch"
    DEPLOY_STAGING = "deploy:staging"
    DEPLOY_PRODUCTION = "deploy:production"
    COMMS_INTERNAL = "comms:internal"
    COMMS_EXTERNAL = "comms:external"
    BUDGET_SPEND = "budget:spend"
    BUDGET_EXCEED = "budget:exceed"
    ORG_HIRE = "org:hire"
    ORG_FIRE = "org:fire"
    ORG_PROMOTE = "org:promote"
    VCS_READ = "vcs:read"
    DB_QUERY = "db:query"
    DB_MUTATE = "db:mutate"
    DB_ADMIN = "db:admin"
    ARCH_DECIDE = "arch:decide"


class MergeOrder(StrEnum):
    """Order in which workspace branches are merged back.

    Determines the sequence of merge operations when multiple
    agent workspaces are being merged into the base branch.
    """

    COMPLETION = "completion"
    PRIORITY = "priority"
    MANUAL = "manual"


class ConflictEscalation(StrEnum):
    """Strategy for handling merge conflicts during workspace merges.

    Controls whether merging stops for human review or continues
    with an automated review agent flagging conflicts.
    """

    HUMAN = "human"
    REVIEW_AGENT = "review_agent"


class ApprovalStatus(StrEnum):
    """Status of a human approval item."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalRiskLevel(StrEnum):
    """Risk level assigned to an approval item."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ConflictType(StrEnum):
    """Type of merge conflict detected during workspace merges."""

    TEXTUAL = "textual"
    SEMANTIC = "semantic"


class AutonomyLevel(StrEnum):
    """Autonomy level controlling approval routing for agents.

    Determines which actions an agent can execute autonomously vs.
    which require human or security-agent approval (DESIGN_SPEC §12.2).
    """

    FULL = "full"
    SEMI = "semi"
    SUPERVISED = "supervised"
    LOCKED = "locked"


# Ordering: LOCKED (most restrictive) < SUPERVISED < SEMI < FULL (least restrictive).
_AUTONOMY_RANK: dict[AutonomyLevel, int] = {
    AutonomyLevel.LOCKED: 0,
    AutonomyLevel.SUPERVISED: 1,
    AutonomyLevel.SEMI: 2,
    AutonomyLevel.FULL: 3,
}


def compare_autonomy(a: AutonomyLevel, b: AutonomyLevel) -> int:
    """Compare two autonomy levels.

    Returns negative if *a* is more restrictive than *b*, zero if equal,
    positive if *a* is less restrictive than *b*.

    Args:
        a: First autonomy level.
        b: Second autonomy level.

    Returns:
        Integer indicating relative autonomy.
    """
    return _AUTONOMY_RANK[a] - _AUTONOMY_RANK[b]


class DowngradeReason(StrEnum):
    """Reason an agent's autonomy was downgraded at runtime."""

    HIGH_ERROR_RATE = "high_error_rate"
    BUDGET_EXHAUSTED = "budget_exhausted"
    SECURITY_INCIDENT = "security_incident"


class TimeoutActionType(StrEnum):
    """Action to take when an approval item times out (DESIGN_SPEC §12.4)."""

    WAIT = "wait"
    APPROVE = "approve"
    DENY = "deny"
    ESCALATE = "escalate"
