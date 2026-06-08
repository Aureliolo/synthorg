# module-kind: declarative
"""Domain enumerations for the SynthOrg framework."""

from enum import StrEnum


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
    CONSULTANCY = "consultancy"
    DATA_TEAM = "data_team"
    CUSTOM = "custom"


class SkillPattern(StrEnum):
    """Skill interaction patterns for company templates.

    Based on the five-pattern taxonomy: Tool Wrapper, Generator,
    Reviewer, Inversion, and Pipeline.

    Attributes:
        TOOL_WRAPPER: On-demand domain expertise; agents
            self-direct using specialized context.
        GENERATOR: Consistent structured output from reusable
            templates.
        REVIEWER: Modular rubric-based evaluation; separates
            what to check from how to check it.
        INVERSION: Agent interviews user before acting;
            structured requirements gathering.
        PIPELINE: Strict sequential workflow with hard
            checkpoints between stages.
    """

    TOOL_WRAPPER = "tool_wrapper"
    GENERATOR = "generator"
    REVIEWER = "reviewer"
    INVERSION = "inversion"
    PIPELINE = "pipeline"


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
    ``synthorg.core.task_transitions.VALID_TRANSITIONS``.
    Summary for quick reference:

        CREATED -> ASSIGNED | REJECTED
        ASSIGNED -> IN_PROGRESS | AUTH_REQUIRED | BLOCKED | CANCELLED
                    | FAILED | INTERRUPTED | SUSPENDED
        IN_PROGRESS -> IN_REVIEW | AUTH_REQUIRED | BLOCKED | CANCELLED
                       | FAILED | INTERRUPTED | SUSPENDED
        IN_REVIEW -> COMPLETED | IN_PROGRESS (rework) | BLOCKED | CANCELLED
        AUTH_REQUIRED -> ASSIGNED (approved) | CANCELLED (denied/timeout)
        BLOCKED -> ASSIGNED (unblocked)
        FAILED -> ASSIGNED (reassignment for retry)
        INTERRUPTED -> ASSIGNED (reassignment on restart)
        SUSPENDED -> ASSIGNED (resume from checkpoint)
        COMPLETED, CANCELLED, and REJECTED are terminal states.
        FAILED, INTERRUPTED, and SUSPENDED are non-terminal (can be reassigned).
        AUTH_REQUIRED is non-terminal (waiting for authorization).
    """

    CREATED = "created"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    AUTH_REQUIRED = "auth_required"


class TaskType(StrEnum):
    """Classification of the kind of work a task represents."""

    DEVELOPMENT = "development"
    DESIGN = "design"
    RESEARCH = "research"
    REVIEW = "review"
    MEETING = "meeting"
    ADMIN = "admin"
    ANALYSIS = "analysis"


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


class Stakes(StrEnum):
    """How consequential a subtask or task is for stakes-aware routing.

    Distinct from :class:`Priority` (urgency/importance) and
    :class:`Complexity` (effort): stakes captures the *cost of being
    wrong*. Low-stakes work tolerates a cheap model; high-stakes work
    (architecture, irreversible decisions) warrants a strong model and
    an adversarial red-team review. The authoritative ordering lives in
    ``_STAKES_ORDER`` below.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


# Ordering: LOW < NORMAL < HIGH < CRITICAL. Explicit literal (not a
# dynamic tuple(Stakes)) so the sync guard below is not tautological and
# a new member forces a conscious placement here.
_STAKES_ORDER: tuple[Stakes, ...] = (
    Stakes.LOW,
    Stakes.NORMAL,
    Stakes.HIGH,
    Stakes.CRITICAL,
)

# Fail loudly if the ordering tuple drifts from the enum membership.
# The symmetric difference names whichever members are out of sync.
if set(_STAKES_ORDER) != set(Stakes):
    _stakes_msg = f"_STAKES_ORDER out of sync: {set(_STAKES_ORDER) ^ set(Stakes)}"
    raise RuntimeError(_stakes_msg)

_STAKES_RANK: dict[Stakes, int] = {
    level: idx for idx, level in enumerate(_STAKES_ORDER)
}


def compare_stakes(a: Stakes, b: Stakes) -> int:
    """Compare two stakes levels.

    Returns negative if *a* is lower-stakes than *b*, zero if equal,
    positive if *a* is higher-stakes than *b*.

    Args:
        a: First stakes level.
        b: Second stakes level.

    Returns:
        Integer indicating relative stakes.
    """
    return _STAKES_RANK[a] - _STAKES_RANK[b]


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


class GitBackendType(StrEnum):
    """Discriminator selecting how a project's git repository is stored.

    ``EMBEDDED`` is the safe default: the product self-hosts a bare repo
    on the persistent volume, with no external dependency.  ``LOCAL_PATH``
    targets a caller-supplied repository on disk.  ``EXTERNAL_REMOTE``
    delegates to an external forge remote resolved via the connection
    catalogue.
    """

    EMBEDDED = "embedded"
    EXTERNAL_REMOTE = "external_remote"
    LOCAL_PATH = "local_path"


class EnvironmentType(StrEnum):
    """Discriminator selecting how a project declares its dev environment.

    ``MANIFEST`` is the safe default: a backend-agnostic bootstrap manifest
    (committed lockfiles plus ordered setup commands) that provisions into
    the mounted workspace and runs in both the subprocess and Docker
    sandboxes, and emits a stock ``bootstrap.sh`` so a fresh clone is
    reproducible without the product present.  ``DEVCONTAINER`` builds a
    sealed Docker image from ``.devcontainer/devcontainer.json`` (Docker
    backend only).  ``NIX`` provisions a hermetic environment from a
    ``flake.nix`` via ``nix develop``.
    """

    MANIFEST = "manifest"
    DEVCONTAINER = "devcontainer"
    NIX = "nix"


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
    MEMORY = "memory"
    ONTOLOGY = "ontology"
    MCP = "mcp"
    BROWSER = "browser"
    EXTERNAL_DATA = "external_data"
    DESKTOP = "desktop"
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
    and execution ordering. See the Engine design page.
    """

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    MIXED = "mixed"


class CoordinationTopology(StrEnum):
    """Coordination topology for multi-agent task execution.

    Determines how agents coordinate when executing decomposed subtasks.
    See the Engine design page.
    """

    SAS = "sas"
    CENTRALIZED = "centralized"
    DECENTRALIZED = "decentralized"
    CONTEXT_DEPENDENT = "context_dependent"
    AUTO = "auto"


class ActionType(StrEnum):
    """Two-level action type taxonomy for security classification.

    Used by autonomy presets (see Operations design page), SecOps
    validation, tiered timeout policies, and progressive trust.
    Values follow a ``category:action`` naming convention.

    Custom action type strings are also accepted by models that use
    ``str`` for ``action_type`` fields -- these enum members are
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
    TOOL_CREATE = "tool:create"
    MEMORY_READ = "memory:read"
    KNOWLEDGE_INGEST = "knowledge:ingest"
    KNOWLEDGE_REINDEX = "knowledge:reindex"
    BROWSER_NAVIGATE = "browser:navigate"
    BROWSER_SCREENSHOT = "browser:screenshot"
    BROWSER_DIFF = "browser:diff"
    BROWSER_ACCESSIBILITY_SCAN = "browser:accessibility_scan"
    BROWSER_SPEC = "browser:spec"
    EXTERNAL_DATA_REQUEST = "external_data:request"
    RESEARCH_RUN = "research:run"
    DESKTOP_LAUNCH = "desktop:launch"
    DESKTOP_CLICK = "desktop:click"
    DESKTOP_TYPE = "desktop:type"
    DESKTOP_KEY = "desktop:key"
    DESKTOP_SCREENSHOT = "desktop:screenshot"
    DESKTOP_SCROLL = "desktop:scroll"


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


class ApprovalSource(StrEnum):
    """Origin of an approval item, fixed at creation.

    Routing of a decided approval (mid-execution resume vs. review
    gate) keys off this persisted discriminator rather than a live
    parked-context probe, keeping the flow deterministic.

    Attributes:
        PARKED_CONTEXT: Backs a parked agent execution context (SecOps
            escalation or ``request_human_approval``); resumes the run.
        REVIEW_GATE: Any other approval (autonomy, hiring, promotion,
            scaling, ...); drives the review-gate transition. Default.
        CONVERSATIONAL_INTAKE: A work item proposed via the
            conversational interface; approval rebuilds the ``WorkItem``
            and runs it through the pipeline, rejection declines it.
        CONVERSATIONAL_INVITE: An agent's request to add another agent
            to a group conversation; approval adds the participant +
            hands over the transcript, rejection leaves membership.
    """

    PARKED_CONTEXT = "parked_context"
    REVIEW_GATE = "review_gate"
    CONVERSATIONAL_INTAKE = "conversational_intake"
    CONVERSATIONAL_INVITE = "conversational_invite"


class ConversationRole(StrEnum):
    """Author of a single conversational turn.

    Attributes:
        USER: A human message into the conversation.
        ASSISTANT: A Chief of Staff reply (generic 1:1 + routed paths).
        AGENT: A named role agent's group-chat contribution; the author
            is recorded in the turn's ``author_agent_id`` / name fields.
    """

    USER = "user"
    ASSISTANT = "assistant"
    AGENT = "agent"


class ConversationStatus(StrEnum):
    """Lifecycle state of a Chief of Staff conversation.

    Attributes:
        ACTIVE: Open for further turns; the clarify loop may continue.
        PROPOSED: At least one work item proposed into the approval
            queue from this conversation.
        CLOSED: Terminal; no further turns are accepted.
    """

    ACTIVE = "active"
    PROPOSED = "proposed"
    CLOSED = "closed"


class ConversationalProposalStatus(StrEnum):
    """Lifecycle state of a conversational work proposal.

    Attributes:
        PENDING: Awaiting the human approval decision.
        EXECUTING: Approved, pipeline run in flight. Acquired via
            PENDING -> EXECUTING CAS so only one concurrent decision
            drives the pipeline; reverted to PENDING on failure
            (retryable) or advanced to EXECUTED on success.
        EXECUTED: Approved; the work item ran through the pipeline.
        REJECTED: Declined; the work item never reached the pipeline.
    """

    PENDING = "pending"
    EXECUTING = "executing"
    EXECUTED = "executed"
    REJECTED = "rejected"


class CharterStatus(StrEnum):
    """Lifecycle state of a project charter produced by a deep interview.

    Attributes:
        DRAFTED: The interview produced a charter draft; the user may
            review and edit it in place. The only non-terminal state.
        APPROVED: The charter was approved and dispatched into the work
            pipeline spine as a real project run. Terminal.
        CANCELLED: The charter was discarded before approval. Terminal.
    """

    DRAFTED = "drafted"
    APPROVED = "approved"
    CANCELLED = "cancelled"


class ConflictType(StrEnum):
    """Type of merge conflict detected during workspace merges."""

    TEXTUAL = "textual"
    SEMANTIC = "semantic"


class AutonomyLevel(StrEnum):
    """Autonomy level controlling approval routing for agents.

    Determines which actions an agent can execute autonomously vs.
    which require human or security-agent approval (see Operations design page).
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
    RISK_BUDGET_EXHAUSTED = "risk_budget_exhausted"
    SECURITY_INCIDENT = "security_incident"


class FailureCategory(StrEnum):
    """Machine-readable failure classification for recovery results.

    Used by ``RecoveryResult`` to provide structured failure diagnosis
    that enables smarter checkpoint reconciliation and task reassignment
    routing.  ``UNKNOWN`` is the honest default for error messages that
    cannot be confidently classified -- it is explicit rather than a
    silent ``TOOL_FAILURE`` lie.
    """

    TOOL_FAILURE = "tool_failure"
    STAGNATION = "stagnation"
    BUDGET_EXCEEDED = "budget_exceeded"
    QUALITY_GATE_FAILED = "quality_gate_failed"
    TIMEOUT = "timeout"
    DELEGATION_FAILED = "delegation_failed"
    UNKNOWN = "unknown"


class DecisionOutcome(StrEnum):
    """Outcome of a review gate decision.

    Used by ``DecisionRecord`` for the auditable decisions drop-box.
    """

    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_APPROVED = "auto_approved"
    AUTO_REJECTED = "auto_rejected"
    ESCALATED = "escalated"


class ExecutionStatus(StrEnum):
    """Runtime execution status of an agent.

    Tracks whether an agent is currently executing, paused (e.g. waiting
    for approval), or idle.  Used by ``AgentRuntimeState`` for dashboard
    queries and graceful-shutdown discovery.
    """

    IDLE = "idle"
    EXECUTING = "executing"
    PAUSED = "paused"


class TimeoutActionType(StrEnum):
    """Action to take when an approval item times out (see Operations design page)."""

    WAIT = "wait"
    APPROVE = "approve"
    DENY = "deny"
    ESCALATE = "escalate"


class TaskSource(StrEnum):
    """Origin of a task within the system.

    Distinguishes tasks created internally by agents from those
    originating from client simulation or external API calls.
    """

    INTERNAL = "internal"
    CLIENT = "client"
    SIMULATION = "simulation"


class InterventionKind(StrEnum):
    """Operator intervention applied from the mission-control cockpit.

    PAUSE and KILL reuse the task lifecycle seams (transition to
    ``INTERRUPTED`` / cancel to ``CANCELLED``). HINT and REDIRECT route
    through the steering directive: both post an ``INFO_REQUEST``
    interrupt the engine consumes at the next safe turn boundary, so the
    operator's text reaches the running agent without corrupting state.
    """

    PAUSE = "pause"
    KILL = "kill"
    HINT = "hint"
    REDIRECT = "redirect"
