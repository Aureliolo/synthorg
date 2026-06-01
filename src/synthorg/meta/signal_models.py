"""Signal summary models for the self-improvement meta-loop.

Typed summaries aggregated from 7 org-wide signal domains
(performance, budget, coordination, scaling, errors, evolution,
telemetry) plus the composite snapshot passed to the rule engine.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from synthorg.core.types import NotBlankStr


class TrendDirection(StrEnum):
    """Direction of a metric trend."""

    IMPROVING = "improving"
    DECLINING = "declining"
    STABLE = "stable"


class MetricSummary(BaseModel):
    """Summary of a single metric across the org.

    Attributes:
        name: Metric name.
        value: Current aggregate value.
        trend: Direction over the observation window.
        window_days: How many days the trend covers.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr
    value: float
    trend: TrendDirection = TrendDirection.STABLE
    window_days: int = Field(default=7, ge=1)


class OrgPerformanceSummary(BaseModel):
    """Org-wide performance signal summary.

    Attributes:
        avg_quality_score: Org average quality (0-10).
        avg_success_rate: Org average success rate (0-1).
        avg_collaboration_score: Org average collaboration (0-10).
        metrics: Per-metric summaries with trends.
        agent_count: Number of active agents.
        department_summaries: Per-department metric rollups.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    avg_quality_score: float = Field(ge=0.0, le=10.0)
    avg_success_rate: float = Field(ge=0.0, le=1.0)
    avg_collaboration_score: float = Field(ge=0.0, le=10.0)
    metrics: tuple[MetricSummary, ...] = ()
    agent_count: int = Field(ge=0)
    department_summaries: dict[str, dict[str, float]] = Field(
        default_factory=dict,
    )


class OrgBudgetSummary(BaseModel):
    """Org-wide budget signal summary.

    Attributes:
        total_spend: Total spend in current period (configured display currency).
        productive_ratio: Fraction of spend on productive work.
        coordination_ratio: Fraction of spend on coordination.
        system_ratio: Fraction of spend on system overhead.
        days_until_exhausted: Forecast days until budget runs out.
        forecast_confidence: Confidence in the forecast (0-1).
        orchestration_overhead: Coordination/productive token ratio.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    total_spend: float = Field(ge=0.0)
    productive_ratio: float = Field(ge=0.0, le=1.0)
    coordination_ratio: float = Field(ge=0.0, le=1.0)
    system_ratio: float = Field(ge=0.0, le=1.0)
    days_until_exhausted: int | None = None
    forecast_confidence: float = Field(ge=0.0, le=1.0)
    orchestration_overhead: float = Field(ge=0.0)


class OrgCoordinationSummary(BaseModel):
    """Org-wide coordination metrics summary.

    Attributes:
        coordination_efficiency: Success-rate-adjusted turn efficiency.
        coordination_overhead_pct: Percentage overhead for MAS vs SAS.
        error_amplification: MAS/SAS error rate ratio.
        message_density: Messages per reasoning turn.
        redundancy_rate: Mean output similarity (0-1).
        straggler_gap_ratio: Slowest/mean completion ratio.
        sample_count: Number of tasks used for these metrics.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    coordination_efficiency: float | None = None
    coordination_overhead_pct: float | None = None
    error_amplification: float | None = None
    message_density: float | None = None
    redundancy_rate: float | None = None
    straggler_gap_ratio: float | None = None
    sample_count: int = Field(default=0, ge=0)


class ScalingDecisionSummary(BaseModel):
    """Summary of a recent scaling decision and its outcome.

    Attributes:
        decision_id: Stable identifier from the underlying scaling
            decision.  Exposed so consumers and tests can join outcomes
            back to specific decisions without relying on positional
            ordering or other fields that may not be unique.
        action_type: What was proposed (hire/prune/hold).
        outcome: What happened (executed/failed/deferred/rejected).
        source_strategy: Which strategy proposed it.
        rationale: Why.
        created_at: When the decision was made.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    decision_id: NotBlankStr
    action_type: NotBlankStr
    outcome: NotBlankStr
    source_strategy: NotBlankStr
    rationale: NotBlankStr
    created_at: AwareDatetime


class OrgScalingSummary(BaseModel):
    """Org-wide scaling signal summary.

    Attributes:
        recent_decisions: Recent scaling decisions with outcomes.
        total_decisions: Total decisions in the window.
        success_rate: Fraction of decisions that were executed.
        most_common_signal: Most frequently triggered signal.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    recent_decisions: tuple[ScalingDecisionSummary, ...] = ()
    total_decisions: int = Field(default=0, ge=0)
    success_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    most_common_signal: NotBlankStr | None = None


class ErrorCategorySummary(BaseModel):
    """Summary of errors in a single category.

    Attributes:
        category: Error category name.
        count: Number of findings in this category.
        avg_severity: Average severity (low=1, medium=2, high=3).
        trend: Whether this category is increasing or decreasing.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    category: NotBlankStr
    count: int = Field(ge=0)
    avg_severity: float = Field(ge=1.0, le=3.0)
    trend: TrendDirection = TrendDirection.STABLE


class OrgErrorSummary(BaseModel):
    """Org-wide error taxonomy signal summary.

    Attributes:
        total_findings: Total error findings in the window.
        categories: Per-category summaries.
        most_severe_category: Category with highest avg severity.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    total_findings: int = Field(default=0, ge=0)
    categories: tuple[ErrorCategorySummary, ...] = ()
    most_severe_category: NotBlankStr | None = None

    @model_validator(mode="after")
    def _validate_severe_category_exists(self) -> Self:
        """Ensure most_severe_category references an actual category.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if self.most_severe_category:
            names = {c.category for c in self.categories}
            if self.most_severe_category not in names:
                msg = (
                    f"most_severe_category '{self.most_severe_category}' "
                    f"not found in categories"
                )
                raise ValueError(msg)
        return self


class EvolutionOutcomeSummary(BaseModel):
    """Summary of a recent evolution outcome.

    Attributes:
        agent_id: Which agent was evolved.
        axis: Which axis was adapted.
        applied: Whether the adaptation was applied.
        proposed_at: When the proposal was generated.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr
    axis: NotBlankStr
    applied: bool
    proposed_at: AwareDatetime


class OrgEvolutionSummary(BaseModel):
    """Org-wide evolution signal summary.

    Attributes:
        recent_outcomes: Recent evolution outcomes.
        total_proposals: Total proposals in the window.
        approval_rate: Fraction of proposals approved/applied.
        most_adapted_axis: Most frequently adapted axis.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    recent_outcomes: tuple[EvolutionOutcomeSummary, ...] = ()
    total_proposals: int = Field(default=0, ge=0)
    approval_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    most_adapted_axis: NotBlankStr | None = None


class OrgTelemetrySummary(BaseModel):
    """Org-wide telemetry signal summary.

    Attributes:
        event_count: Total telemetry events in the window.
        top_event_types: Most frequent event type names.
        error_event_count: Number of error-level events.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    event_count: int = Field(default=0, ge=0)
    top_event_types: tuple[str, ...] = ()
    error_event_count: int = Field(default=0, ge=0)


class OrgBenchmarkSummary(BaseModel):
    """Org-wide golden-benchmark signal summary.

    Unlike the seven always-on runtime signals, this is an *opt-in,
    offline* signal: it is populated only when a scorecard history
    directory (``meta.scorecard_history_dir``) is configured and has
    recorded golden-company benchmark runs. An all-default (empty)
    instance therefore means "no benchmark history" -- which is why
    :class:`OrgSignalSnapshot` defaults this field rather than requiring
    it (the other summaries describe always-present runtime state).

    Attributes:
        run_count: Number of recorded benchmark runs on the curve.
        latest_total: Most recent run's total score.
        max_total: Maximum achievable score on the latest run.
        score_fraction: Latest run's score as a fraction of the maximum.
        delta: Latest run's total minus the previous run's total.
        is_regression: Whether the latest run regressed against its
            predecessor (drop beyond the regression threshold).
        has_regression: Whether any run on the curve is a regression.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    run_count: int = Field(default=0, ge=0)
    latest_total: int = Field(default=0, ge=0)
    max_total: int = Field(default=0, ge=0)
    delta: int = 0
    is_regression: bool = False
    has_regression: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def score_fraction(self) -> float:
        """Latest run's score as a fraction of the maximum (0.0 with no runs)."""
        return self.latest_total / self.max_total if self.max_total > 0 else 0.0


# ── Composite signal snapshot ──────────────────────────────────────


class OrgSignalSnapshot(BaseModel):
    """Composite snapshot of all org-wide signals.

    Assembled by the snapshot builder from all signal aggregators.
    Passed to the rule engine and improvement strategies.

    Attributes:
        performance: Performance signal summary.
        budget: Budget signal summary.
        coordination: Coordination metrics summary.
        scaling: Scaling signal summary.
        errors: Error taxonomy summary.
        evolution: Evolution signal summary.
        telemetry: Telemetry signal summary.
        benchmark: Golden-benchmark signal summary (opt-in / offline;
            empty when no scorecard history is configured).
        collected_at: When the snapshot was assembled.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    performance: OrgPerformanceSummary
    budget: OrgBudgetSummary
    coordination: OrgCoordinationSummary
    scaling: OrgScalingSummary
    errors: OrgErrorSummary
    evolution: OrgEvolutionSummary
    telemetry: OrgTelemetrySummary
    benchmark: OrgBenchmarkSummary = Field(default_factory=OrgBenchmarkSummary)
    collected_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )
