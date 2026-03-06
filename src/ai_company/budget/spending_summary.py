"""Spending summary models for aggregated cost reporting.

Provides the aggregation data structures consumed by the CFO agent
(DESIGN_SPEC Section 10.3) for cost reporting and budget monitoring.
Views of :class:`~ai_company.budget.cost_record.CostRecord` data are
aggregated by agent, department, and time period.
"""

from collections import Counter
from datetime import datetime  # noqa: TC003 — required at runtime by Pydantic
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_company.budget.enums import BudgetAlertLevel
from ai_company.core.types import NotBlankStr  # noqa: TC001


class PeriodSpending(BaseModel):
    """Spending aggregation for a specific time period.

    Attributes:
        start: Period start (inclusive).
        end: Period end (exclusive).
        total_cost_usd: Total cost for the period.
        total_input_tokens: Total input tokens consumed.
        total_output_tokens: Total output tokens consumed.
        record_count: Number of cost records aggregated.
    """

    model_config = ConfigDict(frozen=True)

    start: datetime = Field(description="Period start (inclusive)")
    end: datetime = Field(description="Period end (exclusive)")
    total_cost_usd: float = Field(
        default=0.0,
        ge=0.0,
        description="Total cost for the period",
    )
    total_input_tokens: int = Field(
        default=0,
        ge=0,
        description="Total input tokens consumed",
    )
    total_output_tokens: int = Field(
        default=0,
        ge=0,
        description="Total output tokens consumed",
    )
    record_count: int = Field(
        default=0,
        ge=0,
        description="Number of cost records aggregated",
    )

    @model_validator(mode="after")
    def _validate_period_ordering(self) -> Self:
        """Ensure start is strictly before end."""
        if self.start >= self.end:
            msg = (
                f"Period start ({self.start.isoformat()}) "
                f"must be before end ({self.end.isoformat()})"
            )
            raise ValueError(msg)
        return self


class AgentSpending(BaseModel):
    """Spending aggregation for a single agent.

    Attributes:
        agent_id: Agent identifier.
        total_cost_usd: Total cost for this agent.
        total_input_tokens: Total input tokens consumed.
        total_output_tokens: Total output tokens consumed.
        record_count: Number of cost records.
    """

    model_config = ConfigDict(frozen=True)

    agent_id: NotBlankStr = Field(description="Agent identifier")
    total_cost_usd: float = Field(
        default=0.0,
        ge=0.0,
        description="Total cost for this agent",
    )
    total_input_tokens: int = Field(
        default=0,
        ge=0,
        description="Total input tokens consumed",
    )
    total_output_tokens: int = Field(
        default=0,
        ge=0,
        description="Total output tokens consumed",
    )
    record_count: int = Field(
        default=0,
        ge=0,
        description="Number of cost records",
    )


class DepartmentSpending(BaseModel):
    """Spending aggregation for a department.

    Attributes:
        department_name: Department name.
        total_cost_usd: Total cost for this department.
        total_input_tokens: Total input tokens consumed.
        total_output_tokens: Total output tokens consumed.
        record_count: Number of cost records.
    """

    model_config = ConfigDict(frozen=True)

    department_name: NotBlankStr = Field(
        description="Department name",
    )
    total_cost_usd: float = Field(
        default=0.0,
        ge=0.0,
        description="Total cost for this department",
    )
    total_input_tokens: int = Field(
        default=0,
        ge=0,
        description="Total input tokens consumed",
    )
    total_output_tokens: int = Field(
        default=0,
        ge=0,
        description="Total output tokens consumed",
    )
    record_count: int = Field(
        default=0,
        ge=0,
        description="Number of cost records",
    )


class SpendingSummary(BaseModel):
    """Top-level spending summary combining all aggregation dimensions.

    Provides a snapshot of spending broken down by time period, agent,
    and department, along with budget utilization context.

    Attributes:
        period: Time-period aggregation.
        by_agent: Per-agent spending breakdown.
        by_department: Per-department spending breakdown.
        budget_total_monthly: Monthly budget for context.
        budget_used_percent: Percent of budget consumed.
        alert_level: Current budget alert level.
    """

    model_config = ConfigDict(frozen=True)

    period: PeriodSpending = Field(description="Time-period aggregation")
    by_agent: tuple[AgentSpending, ...] = Field(
        default=(),
        description="Per-agent spending breakdown",
    )
    by_department: tuple[DepartmentSpending, ...] = Field(
        default=(),
        description="Per-department spending breakdown",
    )
    budget_total_monthly: float = Field(
        default=0.0,
        ge=0.0,
        description="Monthly budget for context",
    )
    budget_used_percent: float = Field(
        default=0.0,
        ge=0.0,
        description="Percent of budget consumed",
    )
    alert_level: BudgetAlertLevel = Field(
        default=BudgetAlertLevel.NORMAL,
        description="Current budget alert level",
    )

    @model_validator(mode="after")
    def _validate_unique_agent_ids(self) -> Self:
        """Ensure no duplicate agent_id values in by_agent."""
        ids = [a.agent_id for a in self.by_agent]
        if len(ids) != len(set(ids)):
            dupes = sorted(i for i, c in Counter(ids).items() if c > 1)
            msg = f"Duplicate agent_id values in by_agent: {dupes}"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_unique_department_names(self) -> Self:
        """Ensure no duplicate department_name values in by_department."""
        names = [d.department_name for d in self.by_department]
        if len(names) != len(set(names)):
            dupes = sorted(n for n, c in Counter(names).items() if c > 1)
            msg = f"Duplicate department_name values in by_department: {dupes}"
            raise ValueError(msg)
        return self
