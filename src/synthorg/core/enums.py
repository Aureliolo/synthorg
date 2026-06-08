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
