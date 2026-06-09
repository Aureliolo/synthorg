"""Automated report template models.

Defines the data models for each report template type: spending,
performance, task completion, risk trends, and comprehensive.

All models are frozen Pydantic models (immutable, append-only pattern).
"""

import datetime as _dt
from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from synthorg.budget.report_config import ReportPeriod
from synthorg.budget.report_models import SpendingReport
from synthorg.core.types import NotBlankStr

# ── Performance ──────────────────────────────────────────────────


class AgentPerformanceSummary(BaseModel):
    """Performance summary for a single agent.

    Attributes:
        agent_id: Agent identifier.
        tasks_completed: Number of tasks completed.
        tasks_failed: Number of tasks failed.
        average_quality_score: Average quality score (None if no data).
        total_cost: Total cost incurred.
        total_risk_units: Total risk units accumulated.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Agent identifier")
    tasks_completed: int = Field(default=0, ge=0)
    tasks_failed: int = Field(default=0, ge=0)
    average_quality_score: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
    )
    total_cost: float = Field(default=0.0, ge=0.0)
    total_risk_units: float = Field(default=0.0, ge=0.0)


class PerformanceMetricsReport(BaseModel):
    """Agent performance metrics report.

    Attributes:
        agent_snapshots: Per-agent performance summaries.
        average_quality_score: Org-wide average quality score.
        average_task_duration_seconds: Average task duration.
        total_tasks_completed: Total tasks completed across all agents.
        total_tasks_failed: Total tasks failed across all agents.
        generated_at: When the report was generated.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_snapshots: tuple[AgentPerformanceSummary, ...] = ()
    average_quality_score: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
    )
    average_task_duration_seconds: float | None = Field(
        default=None,
        ge=0.0,
    )
    total_tasks_completed: int = Field(default=0, ge=0)
    total_tasks_failed: int = Field(default=0, ge=0)
    generated_at: AwareDatetime = Field(description="Generation timestamp")


# ── Task Completion ──────────────────────────────────────────────


class DepartmentTaskSummary(BaseModel):
    """Task completion summary for a single department.

    Attributes:
        department: Department name.
        assigned: Tasks assigned.
        completed: Tasks completed.
        failed: Tasks failed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    department: NotBlankStr = Field(description="Department name")
    assigned: int = Field(default=0, ge=0)
    completed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)


class TaskCompletionReport(BaseModel):
    """Task completion rates report.

    Attributes:
        total_assigned: Total tasks assigned.
        total_completed: Total tasks completed.
        total_failed: Total tasks failed.
        total_in_progress: Tasks still in progress.
        completion_rate: Completion rate percentage (computed).
        by_department: Per-department breakdown.
        generated_at: When the report was generated.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    total_assigned: int = Field(default=0, ge=0)
    total_completed: int = Field(default=0, ge=0)
    total_failed: int = Field(default=0, ge=0)
    total_in_progress: int = Field(default=0, ge=0)
    by_department: tuple[DepartmentTaskSummary, ...] = ()
    generated_at: AwareDatetime = Field(description="Generation timestamp")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def completion_rate(self) -> float:
        """Completion rate as a percentage (0--100)."""
        if self.total_assigned <= 0:
            return 0.0
        return round(self.total_completed / self.total_assigned * 100, 2)


# ── Risk Trends ──────────────────────────────────────────────────


class DailyRiskPoint(BaseModel):
    """Single data point in a daily risk trend.

    Attributes:
        date: The date.
        total_risk_units: Total risk units for the day.
        record_count: Number of risk records.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    date: _dt.date = Field(description="Date")
    total_risk_units: float = Field(default=0.0, ge=0.0)
    record_count: int = Field(default=0, ge=0)


class RiskTrendsReport(BaseModel):
    """Risk accumulation trends report.

    Attributes:
        total_risk_units: Total risk units in the period.
        risk_by_agent: Risk units per agent (sorted descending).
        risk_by_action_type: Risk units per action type (sorted descending).
        daily_risk_trend: Daily risk accumulation trend.
        generated_at: When the report was generated.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    total_risk_units: float = Field(default=0.0, ge=0.0)
    risk_by_agent: tuple[tuple[NotBlankStr, float], ...] = ()
    risk_by_action_type: tuple[tuple[NotBlankStr, float], ...] = ()
    daily_risk_trend: tuple[DailyRiskPoint, ...] = ()
    generated_at: AwareDatetime = Field(description="Generation timestamp")

    @model_validator(mode="after")
    def _validate_agent_ranking_order(self) -> Self:
        """Ensure risk_by_agent is sorted descending.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        values = [v for _, v in self.risk_by_agent]
        if values != sorted(values, reverse=True):
            msg = "risk_by_agent must be sorted by risk_units descending"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_action_type_ranking_order(self) -> Self:
        """Ensure risk_by_action_type is sorted descending.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        values = [v for _, v in self.risk_by_action_type]
        if values != sorted(values, reverse=True):
            msg = "risk_by_action_type must be sorted by risk_units descending"
            raise ValueError(msg)
        return self


# ── Comprehensive ────────────────────────────────────────────────


class ComprehensiveReport(BaseModel):
    """Comprehensive report combining all sub-reports.

    Attributes:
        period: The report period (daily/weekly/monthly).
        start: Period start (inclusive).
        end: Period end (exclusive).
        spending: Spending report (optional).
        performance: Performance metrics report (optional).
        task_completion: Task completion report (optional).
        risk_trends: Risk trends report (optional).
        generated_at: When the report was generated.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    period: ReportPeriod = Field(description="Report period")
    start: AwareDatetime = Field(description="Period start (inclusive)")
    end: AwareDatetime = Field(description="Period end (exclusive)")
    spending: SpendingReport | None = None
    performance: PerformanceMetricsReport | None = None
    task_completion: TaskCompletionReport | None = None
    risk_trends: RiskTrendsReport | None = None
    generated_at: AwareDatetime = Field(description="Generation timestamp")

    @model_validator(mode="after")
    def _validate_time_range(self) -> Self:
        """Ensure start < end.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.start >= self.end:
            msg = (
                f"start ({self.start.isoformat()}) must be before "
                f"end ({self.end.isoformat()})"
            )
            raise ValueError(msg)
        return self
