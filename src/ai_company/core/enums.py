"""Domain enumerations for the AI company framework."""

from enum import StrEnum


class SeniorityLevel(StrEnum):
    """Seniority levels for agents within the organization.

    Each level corresponds to an authority scope, typical model tier, and
    cost tier defined in ``ai_company.core.role_catalog.SENIORITY_INFO``.
    """

    # DESIGN_SPEC Section 3.2 says "Intern/Junior" — collapsed to a single JUNIOR level.
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    PRINCIPAL = "principal"
    DIRECTOR = "director"
    VP = "vp"
    C_SUITE = "c_suite"


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
        ASSIGNED -> IN_PROGRESS | BLOCKED | CANCELLED
        IN_PROGRESS -> IN_REVIEW | BLOCKED | CANCELLED | FAILED
        IN_REVIEW -> COMPLETED | IN_PROGRESS (rework) | BLOCKED | CANCELLED
        BLOCKED -> ASSIGNED (unblocked)
        FAILED -> ASSIGNED (reassignment for retry)
        COMPLETED and CANCELLED are terminal states.
    """

    CREATED = "created"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
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
