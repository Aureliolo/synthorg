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
