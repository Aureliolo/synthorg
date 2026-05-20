"""Domain models for Chief of Staff advanced capabilities.

Defines proposal outcomes, outcome statistics, org-level
inflections, proactive alerts, and chat query/response models
that flow through the CoS learning and monitoring pipelines.
"""

from copy import deepcopy
from typing import Any, Literal, Self
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from synthorg.core.enums import (
    Complexity,
    ConversationalProposalStatus,
    ConversationRole,
    ConversationStatus,
    Priority,
    TaskType,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.meta.models import ProposalAltitude, RuleSeverity  # noqa: TC001

# ── Proposal outcome learning ─────────────────────────────────────


class ProposalOutcome(BaseModel):
    """Records a single proposal approval/rejection decision.

    Stored as episodic memory for the confidence learning pipeline.

    Attributes:
        proposal_id: Unique ID of the decided proposal.
        title: Human-readable proposal title.
        altitude: Proposal altitude (config, architecture, prompt).
        source_rule: Rule that triggered the proposal, if any.
        decision: Human decision: approved or rejected.
        confidence_at_decision: Proposal confidence at decision time.
        decided_at: When the decision was made.
        decided_by: Who made the decision.
        decision_reason: Rationale for the decision, if provided.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    proposal_id: UUID
    title: NotBlankStr
    altitude: ProposalAltitude
    source_rule: NotBlankStr | None = None
    decision: Literal["approved", "rejected"]
    confidence_at_decision: float = Field(ge=0.0, le=1.0)
    decided_at: AwareDatetime
    decided_by: NotBlankStr
    decision_reason: NotBlankStr | None = None


class OutcomeStats(BaseModel):
    """Aggregated approval statistics for a (rule, altitude) pair.

    Computed from stored ``ProposalOutcome`` entries. Used by
    confidence adjusters to blend historical approval rates into
    future proposal confidence scores.

    Attributes:
        rule_name: Name of the triggering rule.
        altitude: Proposal altitude.
        total_proposals: Total decisions recorded.
        approved_count: Number of approved proposals.
        rejected_count: Number of rejected proposals.
        last_updated: Timestamp of the most recent outcome.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    rule_name: NotBlankStr
    altitude: ProposalAltitude
    total_proposals: int = Field(ge=1)
    approved_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    last_updated: AwareDatetime

    @model_validator(mode="after")
    def _validate_counts_sum(self) -> Self:
        """Ensure approved + rejected equals total."""
        if self.approved_count + self.rejected_count != self.total_proposals:
            msg = (
                f"approved_count ({self.approved_count}) + "
                f"rejected_count ({self.rejected_count}) != "
                f"total_proposals ({self.total_proposals})"
            )
            raise ValueError(msg)
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def approval_rate(self) -> float:
        """Fraction of proposals that were approved."""
        return self.approved_count / self.total_proposals


# ── Org-level inflection detection ────────────────────────────────


class OrgInflection(BaseModel):
    """Org-level signal inflection detected between snapshots.

    Emitted when a tracked metric changes by more than the
    configured warning or critical threshold between two
    consecutive signal snapshots.

    Attributes:
        id: Unique inflection identifier.
        severity: WARNING or CRITICAL based on change magnitude.
        affected_domains: Signal domains involved.
        metric_name: Name of the metric that changed.
        old_value: Metric value in the previous snapshot.
        new_value: Metric value in the current snapshot.
        description: Human-readable change description.
        detected_at: When the inflection was detected.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: UUID = Field(default_factory=uuid4)
    severity: RuleSeverity
    affected_domains: tuple[NotBlankStr, ...]
    metric_name: NotBlankStr
    old_value: float
    new_value: float
    description: NotBlankStr
    detected_at: AwareDatetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def change_ratio(self) -> float:
        """Absolute fractional change from old to new value.

        Uses symmetric relative change to handle zero baselines
        without producing infinity.
        """
        if self.old_value == 0.0 and self.new_value == 0.0:
            return 0.0
        return abs(self.new_value - self.old_value) / max(
            abs(self.old_value),
            abs(self.new_value),
        )


# ── Proactive alerts ──────────────────────────────────────────────


class Alert(BaseModel):
    """Proactive alert emitted between scheduled meta-loop cycles.

    Generated by the ``ProactiveAlertService`` when an org-level
    inflection breaches the configured severity threshold.

    Attributes:
        id: Unique alert identifier.
        severity: Alert severity level.
        alert_type: Kind of trigger (inflection, threshold, trend).
        description: Human-readable alert description.
        affected_domains: Signal domains involved.
        signal_context: Contextual signal data (deep-copied).
        recommended_action: Suggested remediation, if any.
        emitted_at: When the alert was emitted.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    severity: RuleSeverity
    alert_type: Literal["inflection", "threshold", "trend"]
    description: NotBlankStr
    affected_domains: tuple[NotBlankStr, ...]
    signal_context: dict[str, Any] = Field(default_factory=dict)
    recommended_action: NotBlankStr | None = None
    emitted_at: AwareDatetime

    def __init__(self, **data: Any) -> None:
        if "signal_context" in data:
            data["signal_context"] = deepcopy(data["signal_context"])
        super().__init__(**data)


# ── Chat interface ────────────────────────────────────────────────


class ChatQuery(BaseModel):
    """Input to the Chief of Staff chat interface.

    ``question`` is always required. ``proposal_id`` routes to
    proposal explanation; ``alert_id`` routes to alert explanation;
    a bare ``question`` triggers free-form signal Q&A.

    Attributes:
        question: User's natural language question (required).
        proposal_id: Proposal to explain (optional).
        alert_id: Alert to explain (optional).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    question: NotBlankStr
    proposal_id: UUID | None = None
    alert_id: UUID | None = None


class ChatResponse(BaseModel):
    """Output from the Chief of Staff chat interface.

    Attributes:
        answer: Natural language response from the LLM.
        sources: Signal domains referenced in the answer.
        confidence: LLM's self-assessed confidence (0-1).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    answer: NotBlankStr
    sources: tuple[NotBlankStr, ...] = ()
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


# ── Conversational clarify + propose ──────────────────────────────


class Conversation(BaseModel):
    """A persisted 1:1 conversation with the Chief of Staff.

    Holds only conversation-level state; the ordered turns live in
    ``ConversationTurn`` rows keyed by ``id``.

    Attributes:
        id: Unique conversation identifier.
        created_by: User id that opened the conversation.
        created_at: When the conversation was opened.
        updated_at: When the most recent turn was appended.
        status: Lifecycle state (active, proposed, closed).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    created_by: NotBlankStr
    created_at: AwareDatetime
    updated_at: AwareDatetime
    status: ConversationStatus = ConversationStatus.ACTIVE


class ConversationTurn(BaseModel):
    """A single ordered turn within a conversation.

    Append-only: turns are never mutated once written. ``sequence``
    is a zero-based monotonic index within the conversation.

    Attributes:
        id: Unique turn identifier.
        conversation_id: Owning conversation id.
        sequence: Zero-based position within the conversation.
        role: Who authored the turn (user or assistant).
        content: The turn text.
        created_at: When the turn was appended.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    conversation_id: NotBlankStr
    sequence: int = Field(ge=0)
    role: ConversationRole
    content: NotBlankStr
    created_at: AwareDatetime


class ProposedWork(BaseModel):
    """A single human-shaped work spec emitted by the clarify step.

    Maps one-to-one onto the buildable fields of the work pipeline's
    ``WorkItem`` entry contract; provenance and ids are added when the
    proposal is accepted, not here.

    Attributes:
        title: Short human-readable work title.
        raw_intent: Detailed request / description body.
        project: Project the work belongs to (optional; resolved at
            acceptance when absent).
        priority: Work priority.
        task_type: Classification of the work type.
        estimated_complexity: Complexity estimate (drives routing).
        acceptance_criteria: Optional acceptance criteria strings.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    title: NotBlankStr
    raw_intent: NotBlankStr
    project: NotBlankStr | None = None
    priority: Priority = Priority.MEDIUM
    task_type: TaskType = TaskType.DEVELOPMENT
    estimated_complexity: Complexity = Complexity.MEDIUM
    acceptance_criteria: tuple[NotBlankStr, ...] = ()


class ProposeDecision(BaseModel):
    """Structured output of one clarify-or-propose model turn.

    Exactly one branch is taken: either the model asks a single
    clarifying question, or it emits one or more concrete work
    proposals. The two are mutually exclusive and exhaustive.

    Attributes:
        needs_clarification: ``True`` when the request is still
            underspecified and a question is being asked.
        clarifying_question: The question to put back to the human;
            required iff ``needs_clarification``.
        proposals: The proposed work items; non-empty iff not
            ``needs_clarification``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    needs_clarification: bool
    clarifying_question: NotBlankStr | None = None
    proposals: tuple[ProposedWork, ...] = ()

    @model_validator(mode="after")
    def _validate_exclusive_branch(self) -> Self:
        """Enforce the clarify-XOR-propose invariant."""
        if self.needs_clarification:
            if self.clarifying_question is None:
                msg = "clarifying_question is required when needs_clarification is True"
                raise ValueError(msg)
            if self.proposals:
                msg = "proposals must be empty when needs_clarification is True"
                raise ValueError(msg)
        else:
            if not self.proposals:
                msg = "proposals must be non-empty when needs_clarification is False"
                raise ValueError(msg)
            if self.clarifying_question is not None:
                msg = (
                    "clarifying_question must be None when needs_clarification is False"
                )
                raise ValueError(msg)
        return self


class ConversationalProposal(BaseModel):
    """A proposed work item parked behind a human approval decision.

    Links one ``ApprovalItem`` (by ``approval_id``) to the serialised
    ``WorkItem`` to run if and only if the human approves. The work
    item is stored as JSON so the approval-decision seam can rebuild
    it without re-running the model.

    Attributes:
        id: Unique proposal identifier.
        conversation_id: Originating conversation id.
        approval_id: The gating approval-queue item id.
        work_item_json: ``WorkItem.model_dump_json()`` payload.
        status: Lifecycle state (pending, executed, rejected).
        created_at: When the proposal was parked.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    conversation_id: NotBlankStr
    approval_id: NotBlankStr
    work_item_json: NotBlankStr
    status: ConversationalProposalStatus = ConversationalProposalStatus.PENDING
    created_at: AwareDatetime


# ── Proposer service boundary ─────────────────────────────────────


class ProposeArgs(BaseModel):
    """Args model for one ``ChiefOfStaffProposer.converse`` turn.

    Attributes:
        message: The human's natural-language message this turn.
        created_by: User id that owns the conversation.
        conversation_id: Existing conversation to continue, or
            ``None`` to open a new one.
        project: Optional project the work belongs to; used only when
            a proposal omits its own project.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    message: NotBlankStr
    created_by: NotBlankStr
    conversation_id: NotBlankStr | None = None
    project: NotBlankStr | None = None


class ProposedApprovalSummary(BaseModel):
    """One parked proposal, summarised for the API response.

    Attributes:
        approval_id: The gating approval-queue item id.
        proposal_id: The conversational proposal id.
        title: Proposed work title.
        task_type: Classification of the proposed work.
        priority: Proposed work priority.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    approval_id: NotBlankStr
    proposal_id: NotBlankStr
    title: NotBlankStr
    task_type: TaskType
    priority: Priority


class ProposeResult(BaseModel):
    """Outcome of one ``converse`` turn.

    Exactly one branch: a clarifying question (conversation stays
    open) or one-or-more parked proposals (conversation moves to
    PROPOSED).

    Attributes:
        conversation_id: The conversation this turn belongs to.
        status: ``"needs_clarification"`` or ``"proposed"``.
        clarifying_question: The question to put to the human; set iff
            ``status == "needs_clarification"``.
        proposals: Parked proposal summaries; non-empty iff
            ``status == "proposed"``.
        conversation_closed: ``True`` when the clarification cap was
            reached and the conversation was force-closed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    conversation_id: NotBlankStr
    status: Literal["needs_clarification", "proposed"]
    clarifying_question: NotBlankStr | None = None
    proposals: tuple[ProposedApprovalSummary, ...] = ()
    conversation_closed: bool = False

    @model_validator(mode="after")
    def _validate_status_payload(self) -> Self:
        """Enforce branch invariants between ``status`` and payload.

        ``needs_clarification``: ``clarifying_question`` is required and
        ``proposals`` must be empty (the conversation stays open for
        another turn). ``proposed``: ``proposals`` must be non-empty
        and ``clarifying_question`` must be ``None`` (the turn parked
        work, no follow-up question to ask). Catches caller mistakes
        at construction instead of letting an ambiguous payload reach
        the API response.
        """
        if self.status == "needs_clarification":
            if self.clarifying_question is None:
                msg = (
                    "clarifying_question is required when "
                    "status is 'needs_clarification'"
                )
                raise ValueError(msg)
            if self.proposals:
                msg = "proposals must be empty when status is 'needs_clarification'"
                raise ValueError(msg)
        else:
            if not self.proposals:
                msg = "proposals must be non-empty when status is 'proposed'"
                raise ValueError(msg)
            if self.clarifying_question is not None:
                msg = "clarifying_question must be None when status is 'proposed'"
                raise ValueError(msg)
        return self
