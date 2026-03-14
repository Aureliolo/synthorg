"""CFO / CostOptimizer domain models.

Frozen Pydantic models for anomaly detection, cost efficiency analysis,
downgrade recommendations, and approval decisions. Used by
:class:`~synthorg.budget.optimizer.CostOptimizer` and
:class:`~synthorg.budget.reports.ReportGenerator`.
"""

from datetime import datetime  # noqa: TC003 — required at runtime by Pydantic
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.budget.enums import BudgetAlertLevel
from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.core.types import NotBlankStr  # noqa: TC001

# ── Enums ─────────────────────────────────────────────────────────


class AnomalyType(StrEnum):
    """Type of spending anomaly detected.

    ``SUSTAINED_HIGH`` and ``RATE_INCREASE`` are reserved for future
    detection algorithms; only ``SPIKE`` is currently produced.
    """

    SPIKE = "spike"
    SUSTAINED_HIGH = "sustained_high"
    RATE_INCREASE = "rate_increase"


class AnomalySeverity(StrEnum):
    """Severity of a detected spending anomaly."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EfficiencyRating(StrEnum):
    """Cost efficiency rating for an agent."""

    EFFICIENT = "efficient"
    NORMAL = "normal"
    INEFFICIENT = "inefficient"


# ── Anomaly Detection ─────────────────────────────────────────────


class SpendingAnomaly(BaseModel):
    """A detected spending anomaly for a single agent.

    Attributes:
        agent_id: Agent exhibiting the anomaly.
        anomaly_type: Classification of the anomaly.
        severity: Severity level of the anomaly.
        description: Human-readable explanation.
        current_value: Spending in the most recent window.
        baseline_value: Mean spending across historical windows.
        deviation_factor: How many standard deviations above baseline.
            Set to 0.0 when the baseline is zero (no historical spending).
        detected_at: Timestamp when the anomaly was detected.
        period_start: Start of the window that triggered the anomaly.
        period_end: End of the window that triggered the anomaly.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    agent_id: NotBlankStr = Field(description="Agent identifier")
    anomaly_type: AnomalyType = Field(description="Anomaly classification")
    severity: AnomalySeverity = Field(description="Severity level")
    description: NotBlankStr = Field(description="Human-readable explanation")
    current_value: float = Field(
        ge=0.0,
        description="Spending in the most recent window",
    )
    baseline_value: float = Field(
        ge=0.0,
        description="Mean spending across historical windows",
    )
    deviation_factor: float = Field(
        ge=0.0,
        description="Standard deviations above baseline",
    )
    detected_at: datetime = Field(description="When the anomaly was detected")
    period_start: datetime = Field(description="Anomalous window start")
    period_end: datetime = Field(description="Anomalous window end")

    @model_validator(mode="after")
    def _validate_period_ordering(self) -> Self:
        """Ensure period_start is strictly before period_end."""
        if self.period_start >= self.period_end:
            msg = (
                f"period_start ({self.period_start.isoformat()}) "
                f"must be before period_end ({self.period_end.isoformat()})"
            )
            raise ValueError(msg)
        return self


class AnomalyDetectionResult(BaseModel):
    """Result of an anomaly detection scan.

    Attributes:
        anomalies: Detected anomalies (may be empty).
        scan_period_start: Start of the scanned period.
        scan_period_end: End of the scanned period.
        agents_scanned: Number of unique agents in the data.
        scan_timestamp: When the scan was performed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    anomalies: tuple[SpendingAnomaly, ...] = Field(
        default=(),
        description="Detected anomalies",
    )
    scan_period_start: datetime = Field(description="Scanned period start")
    scan_period_end: datetime = Field(description="Scanned period end")
    agents_scanned: int = Field(ge=0, description="Unique agents in data")
    scan_timestamp: datetime = Field(description="When the scan ran")

    @model_validator(mode="after")
    def _validate_period_ordering(self) -> Self:
        """Ensure scan_period_start is strictly before scan_period_end."""
        if self.scan_period_start >= self.scan_period_end:
            msg = (
                f"scan_period_start ({self.scan_period_start.isoformat()}) "
                f"must be before scan_period_end "
                f"({self.scan_period_end.isoformat()})"
            )
            raise ValueError(msg)
        return self


# ── Cost Efficiency ───────────────────────────────────────────────


class AgentEfficiency(BaseModel):
    """Cost efficiency metrics for a single agent.

    Attributes:
        agent_id: Agent identifier.
        total_cost_usd: Total cost in the analysis period.
        total_tokens: Total tokens consumed (input + output).
        cost_per_1k_tokens: Cost per 1000 tokens (computed).
        record_count: Number of cost records.
        efficiency_rating: Efficiency classification.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    agent_id: NotBlankStr = Field(description="Agent identifier")
    total_cost_usd: float = Field(
        ge=0.0,
        description="Total cost in the analysis period",
    )
    total_tokens: int = Field(ge=0, description="Total tokens consumed")
    record_count: int = Field(ge=0, description="Number of cost records")
    efficiency_rating: EfficiencyRating = Field(
        description="Efficiency classification",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cost_per_1k_tokens(self) -> float:
        """Cost per 1000 tokens, derived from total_cost and total_tokens."""
        if self.total_tokens == 0:
            return 0.0
        return round(
            self.total_cost_usd / self.total_tokens * 1000,
            BUDGET_ROUNDING_PRECISION,
        )


class EfficiencyAnalysis(BaseModel):
    """Result of a cost efficiency analysis.

    Attributes:
        agents: Per-agent efficiency metrics (sorted by cost_per_1k desc).
        global_avg_cost_per_1k: Global average cost per 1000 tokens.
        analysis_period_start: Start of the analysis period.
        analysis_period_end: End of the analysis period.
        inefficient_agent_count: Number of agents rated INEFFICIENT
            (computed).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    agents: tuple[AgentEfficiency, ...] = Field(
        default=(),
        description="Per-agent efficiency metrics",
    )
    global_avg_cost_per_1k: float = Field(
        ge=0.0,
        description="Global average cost per 1000 tokens",
    )
    analysis_period_start: datetime = Field(description="Analysis period start")
    analysis_period_end: datetime = Field(description="Analysis period end")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def inefficient_agent_count(self) -> int:
        """Number of agents rated INEFFICIENT."""
        return sum(
            1
            for a in self.agents
            if a.efficiency_rating == EfficiencyRating.INEFFICIENT
        )

    @model_validator(mode="after")
    def _validate_period_ordering(self) -> Self:
        """Ensure analysis_period_start is before analysis_period_end."""
        if self.analysis_period_start >= self.analysis_period_end:
            msg = (
                f"analysis_period_start "
                f"({self.analysis_period_start.isoformat()}) "
                f"must be before analysis_period_end "
                f"({self.analysis_period_end.isoformat()})"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_agents_sort_order(self) -> Self:
        """Ensure agents are sorted by cost_per_1k_tokens descending."""
        costs = [a.cost_per_1k_tokens for a in self.agents]
        if costs != sorted(costs, reverse=True):
            msg = "agents must be sorted by cost_per_1k_tokens descending"
            raise ValueError(msg)
        return self


# ── Downgrade Recommendations ─────────────────────────────────────


class DowngradeRecommendation(BaseModel):
    """A model downgrade recommendation for a single agent.

    Attributes:
        agent_id: Agent identifier.
        current_model: Currently used model identifier.
        recommended_model: Recommended cheaper model.
        estimated_savings_per_1k: Estimated savings per 1000 tokens.
        reason: Human-readable explanation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    agent_id: NotBlankStr = Field(description="Agent identifier")
    current_model: NotBlankStr = Field(description="Current model identifier")
    recommended_model: NotBlankStr = Field(
        description="Recommended cheaper model",
    )
    estimated_savings_per_1k: float = Field(
        gt=0.0,
        description="Estimated savings per 1000 tokens",
    )
    reason: NotBlankStr = Field(description="Human-readable explanation")

    @model_validator(mode="after")
    def _validate_different_models(self) -> Self:
        """Ensure current and recommended models differ."""
        if self.current_model == self.recommended_model:
            msg = (
                f"current_model and recommended_model must differ, "
                f"both are {self.current_model!r}"
            )
            raise ValueError(msg)
        return self


class DowngradeAnalysis(BaseModel):
    """Result of a downgrade recommendation analysis.

    Attributes:
        recommendations: Per-agent downgrade recommendations.
        total_estimated_savings_per_1k: Aggregate estimated savings per 1000
            tokens across all recommendations (computed).
        budget_pressure_percent: Current budget utilization percentage.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    recommendations: tuple[DowngradeRecommendation, ...] = Field(
        default=(),
        description="Per-agent downgrade recommendations",
    )
    budget_pressure_percent: float = Field(
        ge=0.0,
        description="Current budget utilization percentage",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_estimated_savings_per_1k(self) -> float:
        """Aggregate estimated savings per 1000 tokens."""
        return round(
            sum(r.estimated_savings_per_1k for r in self.recommendations),
            BUDGET_ROUNDING_PRECISION,
        )


# ── Approval Decision ─────────────────────────────────────────────


class ApprovalDecision(BaseModel):
    """Result of evaluating whether an operation should proceed.

    Attributes:
        approved: Whether the operation is approved.
        reason: Explanation for the decision.
        budget_remaining_usd: Remaining budget in USD (may be negative
            if over budget).
        budget_used_percent: Percentage of budget consumed.
        alert_level: Current budget alert level.
        conditions: Any conditions attached to approval.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    approved: bool = Field(description="Whether the operation is approved")
    reason: NotBlankStr = Field(description="Explanation for the decision")
    budget_remaining_usd: float = Field(
        description="Remaining budget in USD (negative when over budget)",
    )
    budget_used_percent: float = Field(
        ge=0.0,
        description="Percentage of budget consumed",
    )
    alert_level: BudgetAlertLevel = Field(
        description="Current budget alert level",
    )
    conditions: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Conditions attached to approval",
    )


# ── Configuration ─────────────────────────────────────────────────


class CostOptimizerConfig(BaseModel):
    """Configuration for the CostOptimizer service.

    Attributes:
        anomaly_sigma_threshold: Number of standard deviations above mean
            to flag as anomalous.
        anomaly_spike_factor: Multiplier above mean to flag as spike
            (independent of stddev).
        inefficiency_threshold_factor: Factor above global average
            cost_per_1k to flag as inefficient.
        approval_auto_deny_alert_level: Alert level at or above which
            operations are automatically denied.
        approval_warn_threshold_usd: Cost threshold for adding a
            warning condition to approval.  When set to ``0.0``, every
            approved operation receives a "High-cost operation" condition
            (effectively "always warn").
        min_anomaly_windows: Minimum number of historical windows
            required before anomaly detection activates.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    anomaly_sigma_threshold: float = Field(
        default=2.0,
        gt=0.0,
        description="Sigma threshold for anomaly detection",
    )
    anomaly_spike_factor: float = Field(
        default=3.0,
        gt=1.0,
        description="Spike factor multiplier above mean",
    )
    inefficiency_threshold_factor: float = Field(
        default=1.5,
        gt=1.0,
        description="Factor above global avg for inefficiency",
    )
    approval_auto_deny_alert_level: BudgetAlertLevel = Field(
        default=BudgetAlertLevel.HARD_STOP,
        description="Alert level triggering auto-deny",
    )
    approval_warn_threshold_usd: float = Field(
        default=1.0,
        ge=0.0,
        description="Cost threshold for warning condition",
    )
    min_anomaly_windows: int = Field(
        default=3,
        ge=2,
        strict=True,
        description="Minimum historical windows for anomaly detection",
    )


# ── Routing Optimization ────────────────────────────────────────


class RoutingSuggestion(BaseModel):
    """A routing optimization suggestion for a single agent.

    Suggests switching an agent's most-used model to a cheaper
    alternative that provides sufficient context window size.

    Attributes:
        agent_id: Agent identifier.
        current_model: Currently most-used model identifier.
        suggested_model: Suggested cheaper alternative.
        current_cost_per_1k: Current model's total cost per 1k tokens.
        suggested_cost_per_1k: Suggested model's total cost per 1k tokens.
        estimated_savings_per_1k: Estimated savings per 1k tokens (computed).
        reason: Human-readable explanation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    agent_id: NotBlankStr = Field(description="Agent identifier")
    current_model: NotBlankStr = Field(description="Current most-used model")
    suggested_model: NotBlankStr = Field(description="Suggested cheaper model")
    current_cost_per_1k: float = Field(
        ge=0.0,
        description="Current model total cost per 1k tokens",
    )
    suggested_cost_per_1k: float = Field(
        ge=0.0,
        description="Suggested model total cost per 1k tokens",
    )
    reason: NotBlankStr = Field(description="Human-readable explanation")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def estimated_savings_per_1k(self) -> float:
        """Estimated savings per 1k tokens."""
        return round(
            self.current_cost_per_1k - self.suggested_cost_per_1k,
            BUDGET_ROUNDING_PRECISION,
        )

    @model_validator(mode="after")
    def _validate_different_models(self) -> Self:
        """Ensure current and suggested models differ."""
        if self.current_model == self.suggested_model:
            msg = (
                f"current_model and suggested_model must differ, "
                f"both are {self.current_model!r}"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_savings_positive(self) -> Self:
        """Ensure suggested model is actually cheaper."""
        if self.suggested_cost_per_1k >= self.current_cost_per_1k:
            msg = (
                f"suggested_cost_per_1k ({self.suggested_cost_per_1k}) "
                f"must be less than current_cost_per_1k "
                f"({self.current_cost_per_1k})"
            )
            raise ValueError(msg)
        return self


class RoutingOptimizationAnalysis(BaseModel):
    """Result of a routing optimization analysis.

    Attributes:
        suggestions: Per-agent routing optimization suggestions.
        total_estimated_savings_per_1k: Aggregate estimated savings per 1k
            tokens across all suggestions (computed).
        analysis_period_start: Start of the analysis period.
        analysis_period_end: End of the analysis period.
        agents_analyzed: Number of agents analyzed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    suggestions: tuple[RoutingSuggestion, ...] = Field(
        default=(),
        description="Per-agent routing optimization suggestions",
    )
    analysis_period_start: datetime = Field(description="Analysis period start")
    analysis_period_end: datetime = Field(description="Analysis period end")
    agents_analyzed: int = Field(ge=0, description="Number of agents analyzed")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_estimated_savings_per_1k(self) -> float:
        """Aggregate estimated savings per 1k tokens."""
        return round(
            sum(s.estimated_savings_per_1k for s in self.suggestions),
            BUDGET_ROUNDING_PRECISION,
        )

    @model_validator(mode="after")
    def _validate_period_ordering(self) -> Self:
        """Ensure analysis_period_start is before analysis_period_end."""
        if self.analysis_period_start >= self.analysis_period_end:
            msg = (
                f"analysis_period_start "
                f"({self.analysis_period_start.isoformat()}) "
                f"must be before analysis_period_end "
                f"({self.analysis_period_end.isoformat()})"
            )
            raise ValueError(msg)
        return self
