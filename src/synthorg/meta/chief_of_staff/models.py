# module-kind: declarative
"""Domain models for Chief of Staff advanced capabilities.

Defines proposal outcomes, outcome statistics, org-level
inflections, proactive alerts, and chat query/response models
that flow through the CoS learning and monitoring pipelines.
"""

from collections.abc import Mapping
from copy import deepcopy
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from synthorg.approval.enums import ApprovalStatus
from synthorg.communication.conversation.enums import (
    ConversationRole,
    ConversationStatus,
)
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task_enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.intervention.enums import InterventionKind
from synthorg.engine.intervention.models import STEERABLE_KINDS
from synthorg.meta.chief_of_staff.enums import ConversationKind, RoutingReason
from synthorg.meta.models import ProposalAltitude, RuleSeverity

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
        """Ensure approved + rejected equals total.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if self.approved_count + self.rejected_count != self.total_proposals:
            msg = (
                f"approved_count ({self.approved_count}) + "
                f"rejected_count ({self.rejected_count}) != "
                f"total_proposals ({self.total_proposals})"
            )
            raise ValueError(msg)
        return self

    @computed_field
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

    @computed_field
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
    signal_context: dict[str, object] = Field(default_factory=dict)
    recommended_action: NotBlankStr | None = None
    emitted_at: AwareDatetime

    @model_validator(mode="before")
    @classmethod
    def _deepcopy_signal_context(cls, data: object) -> object:
        """Deep-copy ``signal_context`` so callers cannot alias internals.

        Runs on every construction path (``Alert(...)`` and
        ``model_validate``) and copies rather than mutating the caller's
        input, which a fragile ``__init__`` override could not guarantee.

        Returns:
            The input data with ``signal_context`` deep-copied when present.
        """
        if isinstance(data, Mapping) and "signal_context" in data:
            return {**data, "signal_context": deepcopy(data["signal_context"])}
        return data


# ── Chat interface ────────────────────────────────────────────────


class ChatQuery(BaseModel):
    """Input to the Chief of Staff chat interface.

    ``question`` is always required. ``alert_id``, when it resolves to
    a persisted alert, routes to dedicated alert explanation.
    ``proposal_id`` cannot route to dedicated proposal explanation (a
    full ``ImprovementProposal`` is not reconstructable from the
    approval queue an approved/pending proposal survives into); when
    it resolves to an approval-queue item, that item's title,
    description, and metadata are folded into the free-form answer's
    context instead. A bare ``question``, or an id that does not
    resolve, triggers plain free-form signal Q&A. Alert takes priority
    when both ids are set.

    Attributes:
        question: User's natural language question (required).
        proposal_id: Approval-queue item to fold into context (optional).
        alert_id: Alert to explain (optional).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    question: NotBlankStr
    proposal_id: UUID | None = None
    alert_id: UUID | None = None


_CITED_STATUS_VOCABULARY: Mapping[str, frozenset[str]] = {
    "task": frozenset(s.value for s in TaskStatus),
    "project": frozenset(s.value for s in ProjectStatus),
    "approval": frozenset(s.value for s in ApprovalStatus),
}


class CitedRecord(BaseModel):
    """One org-state record the chat answer is grounded in.

    A machine-readable citation of the in-flight task, active project, or
    pending approval the Chief of Staff drew on, so the dashboard can show
    the exact records an answer draws on rather than a bare
    provenance-domain tag.

    Attributes:
        kind: Which read surface the record came from.
        record_id: The record's stable identifier (task / project /
            approval id).
        label: Human-readable label (task or approval title, project name).
        status: The record's current lifecycle status value; must be a
            valid status for ``kind``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    kind: Literal["task", "project", "approval"]
    record_id: NotBlankStr
    label: NotBlankStr
    status: NotBlankStr

    @model_validator(mode="after")
    def _validate_status_matches_kind(self) -> Self:
        """Reject a status that is not a valid value for ``kind``.

        The producer derives ``status`` from the matching domain enum, so
        a value outside that enum's vocabulary can only be a construction
        bug (e.g. a swapped ``kind`` / ``status`` pair) and would surface
        a nonsensical citation to the operator or the model.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: When ``status`` is not a member of ``kind``'s
                status vocabulary.
        """
        if self.status not in _CITED_STATUS_VOCABULARY[self.kind]:
            msg = f"{self.status!r} is not a valid status for kind {self.kind!r}"
            raise ValueError(msg)
        return self


class ChatResponse(BaseModel):
    """Output from the Chief of Staff chat interface.

    Attributes:
        answer: Natural language response from the LLM.
        sources: Signal / read-surface domains referenced in the answer.
        cited_records: The specific task / project / approval records the
            answer is grounded in (empty for the scoped explain paths).
        confidence: LLM's self-assessed confidence (0-1).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    answer: NotBlankStr
    sources: tuple[NotBlankStr, ...] = ()
    cited_records: tuple[CitedRecord, ...] = ()
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ChatAnswerDelta(BaseModel):
    """One incremental text delta from a streaming chat answer.

    Attributes:
        delta: The next fragment of the answer, in arrival order. Never
            empty (the stream only emits a delta for non-empty content),
            but may be whitespace: a standalone space or newline is a
            legitimate token, so this is ``min_length=1``, not
            ``NotBlankStr``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    delta: str = Field(min_length=1)


class ChatAnswerComplete(ChatResponse):
    """Terminal event of a streaming chat answer: the assembled result.

    A :class:`ChatResponse` under a distinct type so the streaming union
    (``ChatAnswerDelta | ChatAnswerComplete``) discriminates the terminal
    event from a delta by class, while carrying the identical ``answer`` /
    ``sources`` / ``confidence`` contract as the buffered endpoint.
    """


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
        kind: Conversation shape (direct, routed, or group). Fixed at
            creation; discriminates the 1:1 thread from the concern-routed
            and multi-agent group surfaces. Charter interviews persist
            separately, and acting turns stay part of a direct conversation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    created_by: NotBlankStr
    created_at: AwareDatetime
    updated_at: AwareDatetime
    status: ConversationStatus = ConversationStatus.ACTIVE
    kind: ConversationKind = ConversationKind.DIRECT


class ConversationTurn(BaseModel):
    """A single ordered turn within a conversation.

    Append-only: turns are never mutated once written. ``sequence``
    is a zero-based monotonic index within the conversation.

    Attributes:
        id: Unique turn identifier.
        conversation_id: Owning conversation id.
        sequence: Zero-based position within the conversation.
        role: Who authored the turn (user, assistant, or agent).
        content: The turn text.
        author_agent_id: For ``AGENT`` turns (and routed ``ASSISTANT``
            turns), the id of the responding role agent; ``None`` for
            the generic Chief of Staff persona.
        author_name: Human-readable name of the responding agent, when
            attributed; ``None`` for the generic persona.
        routed_topic: For a concern-routed turn, the classified topic
            label that selected the responding role; ``None`` when not
            routed.
        routing_confidence: Classifier confidence (0-1) for the routed
            topic; ``None`` when not routed.
        created_at: When the turn was appended.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    conversation_id: NotBlankStr
    sequence: int = Field(ge=0)
    role: ConversationRole
    content: NotBlankStr
    author_agent_id: NotBlankStr | None = None
    author_name: NotBlankStr | None = None
    routed_topic: NotBlankStr | None = None
    routing_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    created_at: AwareDatetime


class ProposedWork(BaseModel):
    """A single human-shaped work brief emitted by the clarify step.

    Maps onto the buildable fields of the work pipeline's ``WorkItem``
    entry contract. The brief is not fragmented into approvable pieces:
    it becomes ONE objective that the owner decomposes into a single
    ``Plan``, reviewed holistically in Plan Review. Provenance and ids
    are added when the plan is drafted, not here.

    Attributes:
        title: Short human-readable work title.
        raw_intent: Detailed request / description body.
        project: Project the work belongs to (optional; provisioned at
            plan-draft time when absent).
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


class ProposedSteering(BaseModel):
    """A single steering directive emitted by the clarify step.

    The human asks to redirect or hint an in-flight project rather than to
    create new work. On approval this routes to ``SteeringService.issue``;
    supersession stays an explicit operator cockpit action, so the
    conversational path issues in ``NONE`` supersede mode.

    Attributes:
        project: Project to steer (optional; resolved at acceptance when
            absent, mirroring ``ProposedWork.project``).
        kind: ``HINT`` (advisory) or ``REDIRECT`` (forces a re-plan).
        text: The directive text the agents adopt.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project: NotBlankStr | None = None
    kind: InterventionKind
    text: NotBlankStr

    @model_validator(mode="after")
    def _validate_steerable_kind(self) -> Self:
        """Reject PAUSE/KILL: only HINT and REDIRECT are steerable.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: When ``kind`` is not a steerable intervention.
        """
        if self.kind not in STEERABLE_KINDS:
            msg = f"{self.kind.value!r} is not a steerable directive kind"
            raise ValueError(msg)
        return self


class ProposeDecision(BaseModel):
    """Structured output of one clarify-or-propose model turn.

    Exactly one branch is taken: either the model asks a single clarifying
    question, or it emits ONE concrete work brief and/or one-or-more steering
    directives. A single conversational request becomes a single objective
    (one ``Plan``, reviewed as a whole), never a set of independently
    approvable pieces. Clarification is mutually exclusive with proposing.

    Attributes:
        needs_clarification: ``True`` when the request is still
            underspecified and a question is being asked.
        clarifying_question: The question to put back to the human;
            required iff ``needs_clarification``.
        work: The single work brief to draft a plan for; ``None`` when the
            turn only steers an in-flight project.
        steering: The proposed steering directives (redirect / hint an
            in-flight project). At least one of ``work`` / ``steering``
            is present iff not ``needs_clarification``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    needs_clarification: bool
    clarifying_question: NotBlankStr | None = None
    work: ProposedWork | None = None
    steering: tuple[ProposedSteering, ...] = ()

    @model_validator(mode="after")
    def _validate_exclusive_branch(self) -> Self:
        """Enforce the clarify-XOR-propose invariant.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if self.needs_clarification:
            if self.clarifying_question is None:
                msg = "clarifying_question is required when needs_clarification is True"
                raise ValueError(msg)
            if self.work is not None or self.steering:
                msg = "work/steering must be empty when needs_clarification is True"
                raise ValueError(msg)
        else:
            if self.work is None and not self.steering:
                msg = (
                    "work or steering must be present when needs_clarification is False"
                )
                raise ValueError(msg)
            if self.clarifying_question is not None:
                msg = (
                    "clarifying_question must be None when needs_clarification is False"
                )
                raise ValueError(msg)
        return self


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


class PlanDraftSummary(BaseModel):
    """The plan-draft handoff for one accepted work brief.

    A conversational request that clears clarification becomes ONE
    objective task whose owner drafts a single ``Plan``; the plan is
    then parked for holistic human review (Plan Review), never a set of
    per-item approvals. This summary names the objective task the plan is
    being drafted for so the chat can deep-link the operator to the plan
    once decomposition completes.

    Attributes:
        task_id: The objective task the plan is being drafted for; the
            chat subscribes to its progress stream and the parked
            plan-review approval carries the same id.
        project: The project the work was filed under (provisioned when
            the request named none).
        title: The objective title.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task_id: NotBlankStr
    project: NotBlankStr
    title: NotBlankStr


class SteeringProposalSummary(BaseModel):
    """One parked steering directive, summarised for the API response.

    Attributes:
        approval_id: The gating approval-queue item id.
        kind: ``HINT`` or ``REDIRECT``.
        text: The directive text awaiting approval.
        project: Project the directive targets.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    approval_id: NotBlankStr
    kind: InterventionKind
    text: NotBlankStr
    project: NotBlankStr


class ProposeResult(BaseModel):
    """Outcome of one ``converse`` turn.

    Exactly one branch: a clarifying question (conversation stays open),
    or the turn acts (conversation moves to PROPOSED) by drafting a plan
    for a work brief and/or parking steering directives.

    Attributes:
        conversation_id: The conversation this turn belongs to.
        status: ``"needs_clarification"`` or ``"proposed"``.
        clarifying_question: The question to put to the human; set iff
            ``status == "needs_clarification"``.
        plan_draft: The plan-draft handoff when the turn accepted a work
            brief (its owner is drafting a single ``Plan`` for holistic
            Plan Review); ``None`` on a steering-only or clarification turn.
        conversation_closed: ``True`` when the clarification cap was
            reached and the conversation was force-closed.
        responder_role: Role of the agent that answered this turn when
            the message was concern-routed; ``None`` for the generic
            Chief of Staff responder (routing off or below the
            confidence floor).
        responder_name: Display name of the responding role agent when
            routed; ``None`` for the generic persona.
        routed_topic: Classified concern label that selected the
            responding role; ``None`` when not routed.
        routing_confidence: Classifier confidence (0-1) for the routed
            topic; ``None`` when not routed.
        routing_reason: Why this turn was, or was not, routed to a role
            agent (``ROUTED`` on success, else the fallback cause).
            ``None`` only on a force-closed turn, where routing is moot.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    conversation_id: NotBlankStr
    status: Literal["needs_clarification", "proposed"]
    clarifying_question: NotBlankStr | None = None
    plan_draft: PlanDraftSummary | None = None
    steering: tuple[SteeringProposalSummary, ...] = ()
    conversation_closed: bool = False
    responder_role: NotBlankStr | None = None
    responder_name: NotBlankStr | None = None
    routed_topic: NotBlankStr | None = None
    routing_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    routing_reason: RoutingReason | None = None

    @model_validator(mode="after")
    def _validate_status_payload(self) -> Self:
        """Enforce branch invariants between ``status`` and payload.

        ``needs_clarification``: ``clarifying_question`` is required and
        both ``plan_draft`` and ``steering`` must be empty (the
        conversation stays open for another turn). ``proposed``: at least
        one of ``plan_draft`` / ``steering`` must be present and
        ``clarifying_question`` must be ``None`` (the turn acted, no
        follow-up question to ask). Catches caller mistakes at
        construction instead of letting an ambiguous payload reach the
        API response.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if self.status == "needs_clarification":
            if self.clarifying_question is None:
                msg = (
                    "clarifying_question is required when "
                    "status is 'needs_clarification'"
                )
                raise ValueError(msg)
            if self.plan_draft is not None or self.steering:
                msg = (
                    "plan_draft/steering must be empty when "
                    "status is 'needs_clarification'"
                )
                raise ValueError(msg)
        else:
            if self.plan_draft is None and not self.steering:
                msg = "plan_draft or steering must be present when status is 'proposed'"
                raise ValueError(msg)
            if self.clarifying_question is not None:
                msg = "clarifying_question must be None when status is 'proposed'"
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_routing_attribution(self) -> Self:
        """Keep the routing attribution consistent with ``routing_reason``.

        The four attribution fields (responder role/name, routed topic,
        confidence) are populated together from a routed decision, so they
        are all present iff ``routing_reason`` is ``ROUTED`` and all absent
        otherwise. Mirrors :class:`RoutingOutcome`'s decision/reason
        invariant so a construction bug cannot present a "routed" turn with
        no responder (or a generic turn wearing a role agent's name).

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: When the attribution and ``routing_reason``
                disagree.
        """
        routed = self.routing_reason is RoutingReason.ROUTED
        attribution = (
            self.responder_role,
            self.responder_name,
            self.routed_topic,
            self.routing_confidence,
        )
        if routed and any(field is None for field in attribution):
            msg = "responder attribution is required when routing_reason is ROUTED"
            raise ValueError(msg)
        if not routed and any(field is not None for field in attribution):
            msg = "responder attribution must be empty unless routing_reason is ROUTED"
            raise ValueError(msg)
        return self
