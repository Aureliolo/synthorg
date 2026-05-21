"""Domain enumerations for the SynthOrg framework."""

from enum import StrEnum


class SeniorityLevel(StrEnum):
    """Seniority levels for agents within the organization.

    Each level corresponds to an authority scope, typical model tier, and
    cost tier defined in ``synthorg.core.role_catalog.SENIORITY_INFO``.
    """

    # Agents page lists "Intern/Junior" -- collapsed to JUNIOR.
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


class StrategicOutputMode(StrEnum):
    """Controls how strategic agents frame their recommendations.

    Applies to any agent with a strategic output mode set (C-suite, VP,
    Director, or any agent with an explicit override).

    - ``option_expander``: Present all options with analysis through each lens.
    - ``advisor``: Recommend top 2-3 options with reasoning and caveats.
    - ``decision_maker``: Make a final recommendation with full justification.
    - ``context_dependent``: Resolves based on agent seniority -- C-suite/VP
      maps to ``decision_maker``, others to ``advisor``.
    """

    OPTION_EXPANDER = "option_expander"
    ADVISOR = "advisor"
    DECISION_MAKER = "decision_maker"
    CONTEXT_DEPENDENT = "context_dependent"


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
    """Memory type categories for agent memory (§7.2).

    ``PROJECT_DOC`` is a project-scoped (not agent-scoped) category used
    by the living-documentation engine. Entries are stored under a
    system docs agent_id in the docs namespace and scoped to a project
    via the ``project:<project_id>`` tag, then surfaced transparently
    via ``ProjectAwareMemoryFacade``.

    ``KNOWLEDGE`` is a corpus-scoped (not agent-scoped) category used by
    the knowledge + provenance substrate. Ingested external sources
    (PDFs, web pages, repos, tickets) are chunked and stored under a
    system knowledge agent_id in the knowledge namespace, scoped to a
    project or globally via tags, and carry provenance for citations.
    """

    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    SOCIAL = "social"
    PROJECT_DOC = "project_doc"
    KNOWLEDGE = "knowledge"


class DocType(StrEnum):
    """Living-document type taxonomy.

    ``STATUS_REPORT`` is a periodic or per-task summary agents write to
    record progress and decisions. ``DELIVERABLE`` is an iteratively
    edited artifact the org produces (PRD, design doc, research memo).
    ``KNOWLEDGE_NOTE`` is freeform knowledge captured by agents during
    work. All three share storage, chunking, and indexing; the type
    drives wiki filtering and renderer affordances only.
    """

    STATUS_REPORT = "status_report"
    DELIVERABLE = "deliverable"
    KNOWLEDGE_NOTE = "knowledge_note"


class SourceType(StrEnum):
    """Origin of an ingested knowledge source.

    Drives ``SourceLoader`` selection in the knowledge substrate. A
    ``REPO`` source is a code tree, ``PDF`` / ``WEB`` / ``DESIGN_DOC`` are
    documents, and ``TICKET`` is an issue thread fetched through the
    governed external-API access tool.
    """

    PDF = "pdf"
    WEB = "web"
    REPO = "repo"
    TICKET = "ticket"
    DESIGN_DOC = "design_doc"


class ContentKind(StrEnum):
    """Structural kind of a loaded unit; drives chunker selection.

    ``CODE`` uses the AST-aware chunker, ``DOCUMENT`` the section-aware
    chunker, ``PDF_PAGE`` the page/region chunker, and ``TICKET_THREAD``
    the document chunker over comment text.
    """

    CODE = "code"
    DOCUMENT = "document"
    PDF_PAGE = "pdf_page"
    TICKET_THREAD = "ticket_thread"


class SourceStatus(StrEnum):
    """Ingestion lifecycle state of a knowledge source."""

    PENDING = "pending"
    INDEXED = "indexed"
    STALE = "stale"
    FAILED = "failed"


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
    ENTITY_DEFINITION = "entity_definition"


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


class WorkflowType(StrEnum):
    """Workflow type for organizing task execution.

    Matches the four workflow types defined in the Engine design page
    (docs/design/engine.md, Workflow Types section).
    """

    SEQUENTIAL_PIPELINE = "sequential_pipeline"
    PARALLEL_EXECUTION = "parallel_execution"
    KANBAN = "kanban"
    AGILE_KANBAN = "agile_kanban"


class WorkflowNodeType(StrEnum):
    """Node type in a visual workflow definition.

    Each node represents a step or control-flow element in the
    visual workflow editor.
    """

    START = "start"
    END = "end"
    TASK = "task"
    AGENT_ASSIGNMENT = "agent_assignment"
    CONDITIONAL = "conditional"
    PARALLEL_SPLIT = "parallel_split"
    PARALLEL_JOIN = "parallel_join"
    SUBWORKFLOW = "subworkflow"
    VERIFICATION = "verification"


class WorkflowValueType(StrEnum):
    """Typed value kinds for workflow I/O declarations.

    Used by :class:`WorkflowIODeclaration` to enforce typed contracts
    on subworkflow inputs and outputs at save time and at runtime.
    """

    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    JSON = "json"
    TASK_REF = "task_ref"
    AGENT_REF = "agent_ref"


class WorkflowEdgeType(StrEnum):
    """Edge type connecting nodes in a visual workflow definition.

    Encodes the relationship semantics between workflow nodes.
    """

    SEQUENTIAL = "sequential"
    CONDITIONAL_TRUE = "conditional_true"
    CONDITIONAL_FALSE = "conditional_false"
    PARALLEL_BRANCH = "parallel_branch"
    VERIFICATION_PASS = "verification_pass"  # noqa: S105
    VERIFICATION_FAIL = "verification_fail"
    VERIFICATION_REFER = "verification_refer"


class WorkflowExecutionStatus(StrEnum):
    """Lifecycle status of a workflow execution instance.

    Tracks the overall progress of an activated workflow definition
    from creation through completion or cancellation.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowNodeExecutionStatus(StrEnum):
    """Per-node execution status within a workflow execution.

    Tracks whether each node in the workflow graph has been
    processed, skipped (conditional branch not taken), or
    resulted in a concrete task.
    """

    PENDING = "pending"
    SKIPPED = "skipped"
    TASK_CREATED = "task_created"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    COMPLETED = "completed"
    SUBWORKFLOW_COMPLETED = "subworkflow_completed"


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
    MEMORY_READ = "memory:read"
    KNOWLEDGE_INGEST = "knowledge:ingest"
    KNOWLEDGE_REINDEX = "knowledge:reindex"
    BROWSER_NAVIGATE = "browser:navigate"
    BROWSER_SCREENSHOT = "browser:screenshot"
    BROWSER_DIFF = "browser:diff"
    BROWSER_ACCESSIBILITY_SCAN = "browser:accessibility_scan"
    BROWSER_SPEC = "browser:spec"
    EXTERNAL_DATA_REQUEST = "external_data:request"
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
    parked-context probe, so the flow is deterministic even when the
    parked-context backend is momentarily unavailable.

    Attributes:
        PARKED_CONTEXT: Backs a parked agent execution context (SecOps
            escalation or the ``request_human_approval`` tool); the
            decision resumes the parked run.
        REVIEW_GATE: Any other approval (autonomy, hiring, promotion,
            pruning, scaling, training, signals, ...); the decision
            drives the review-gate transition. Default.
        CONVERSATIONAL_INTAKE: A work item proposed through the
            conversational interface (Chief of Staff clarify + propose);
            approval reconstructs the ``WorkItem`` and runs it through
            the work pipeline. Rejection records the proposal as
            declined and never touches the pipeline.
    """

    PARKED_CONTEXT = "parked_context"
    REVIEW_GATE = "review_gate"
    CONVERSATIONAL_INTAKE = "conversational_intake"


class ConversationRole(StrEnum):
    """Author of a single conversational turn.

    Attributes:
        USER: A human message into the Chief of Staff conversation.
        ASSISTANT: A Chief of Staff reply (clarifying question or a
            summary of the proposed work items).
    """

    USER = "user"
    ASSISTANT = "assistant"


class ConversationStatus(StrEnum):
    """Lifecycle state of a Chief of Staff conversation.

    Attributes:
        ACTIVE: Open for further turns; the clarify loop may continue.
        PROPOSED: At least one work item has been proposed into the
            approval queue from this conversation.
        CLOSED: Terminal; no further turns are accepted.
    """

    ACTIVE = "active"
    PROPOSED = "proposed"
    CLOSED = "closed"


class ConversationalProposalStatus(StrEnum):
    """Lifecycle state of a conversational work proposal.

    Attributes:
        PENDING: Awaiting the human approval decision.
        EXECUTING: Approved and the pipeline run is in flight. Acquired
            via PENDING -> EXECUTING CAS so only one concurrent decision
            ever drives the pipeline for a given proposal; reverted back
            to PENDING on pipeline failure (retryable) or advanced to
            EXECUTED on success.
        EXECUTED: Approved; the work item ran through the pipeline.
        REJECTED: Declined; the work item never reached the pipeline.
    """

    PENDING = "pending"
    EXECUTING = "executing"
    EXECUTED = "executed"
    REJECTED = "rejected"


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
