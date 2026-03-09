"""CFO / CostOptimizer domain models.

Frozen Pydantic models for anomaly detection, cost efficiency analysis,
downgrade recommendations, and approval decisions. Used by
:class:`~ai_company.budget.optimizer.CostOptimizer` and
:class:`~ai_company.budget.reports.ReportGenerator`.
"""

from datetime import datetime  # noqa: TC003 — required at runtime by Pydantic
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_company.budget.enums import BudgetAlertLevel
from ai_company.core.types import NotBlankStr  # noqa: TC001

# ── Enums ─────────────────────────────────────────────────────────


class AnomalyType(StrEnum):
    """Type of spending anomaly detected."""

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

    model_config = ConfigDict(frozen=True)

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
        cost_per_1k_tokens: Cost per 1000 tokens.
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
    cost_per_1k_tokens: float = Field(
        ge=0.0,
        description="Cost per 1000 tokens",
    )
    record_count: int = Field(ge=0, description="Number of cost records")
    efficiency_rating: EfficiencyRating = Field(
        description="Efficiency classification",
    )


class EfficiencyAnalysis(BaseModel):
    """Result of a cost efficiency analysis.

    Attributes:
        agents: Per-agent efficiency metrics (sorted by cost_per_1k desc).
        global_avg_cost_per_1k: Global average cost per 1000 tokens.
        analysis_period_start: Start of the analysis period.
        analysis_period_end: End of the analysis period.
        inefficient_agent_count: Number of agents rated INEFFICIENT.
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
    inefficient_agent_count: int = Field(
        ge=0,
        description="Number of inefficient agents",
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
        ge=0.0,
        description="Estimated savings per 1000 tokens",
    )
    reason: NotBlankStr = Field(description="Human-readable explanation")


class DowngradeAnalysis(BaseModel):
    """Result of a downgrade recommendation analysis.

    Attributes:
        recommendations: Per-agent downgrade recommendations.
        total_estimated_monthly_savings: Aggregate estimated monthly savings.
        budget_pressure_percent: Current budget utilization percentage.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    recommendations: tuple[DowngradeRecommendation, ...] = Field(
        default=(),
        description="Per-agent downgrade recommendations",
    )
    total_estimated_monthly_savings: float = Field(
        ge=0.0,
        description="Aggregate estimated monthly savings",
    )
    budget_pressure_percent: float = Field(
        ge=0.0,
        description="Current budget utilization percentage",
    )


# ── Approval Decision ─────────────────────────────────────────────


class ApprovalDecision(BaseModel):
    """Result of evaluating whether an operation should proceed.

    Attributes:
        approved: Whether the operation is approved.
        reason: Explanation for the decision.
        budget_remaining_usd: Remaining budget in USD.
        budget_used_percent: Percentage of budget consumed.
        alert_level: Current budget alert level.
        conditions: Any conditions attached to approval.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    approved: bool = Field(description="Whether the operation is approved")
    reason: NotBlankStr = Field(description="Explanation for the decision")
    budget_remaining_usd: float = Field(
        description="Remaining budget in USD",
    )
    budget_used_percent: float = Field(
        ge=0.0,
        description="Percentage of budget consumed",
    )
    alert_level: BudgetAlertLevel = Field(
        description="Current budget alert level",
    )
    conditions: tuple[str, ...] = Field(
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
            warning condition to approval.
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
