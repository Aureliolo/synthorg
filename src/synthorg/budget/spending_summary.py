"""Spending summary models for aggregated cost reporting.

Provides the aggregation data structures used by
:class:`~synthorg.budget.tracker.CostTracker` for cost reporting and
designed for consumption by the CFO agent (see Operations design page).
Views of :class:`~synthorg.budget.cost_record.CostRecord` data are
aggregated by agent, department, and time period.
"""

from collections import Counter
from datetime import datetime  # noqa: TC003 — required at runtime by Pydantic
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.budget.enums import BudgetAlertLevel
from synthorg.core.types import NotBlankStr  # noqa: TC001


class _SpendingTotals(BaseModel):
    """Shared aggregation fields for spending summary models.

    Not intended for direct instantiation — subclass with a
    dimension-specific identifier (agent, department, or period).

    Attributes:
        total_cost_usd: Total cost for the aggregation group.
        total_input_tokens: Total input tokens consumed.
        total_output_tokens: Total output tokens consumed.
        record_count: Number of cost records aggregated.
    """

    model_config = ConfigDict(frozen=True)

    total_cost_usd: float = Field(
        default=0.0,
        ge=0.0,
        description="Total cost for the aggregation group",
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


class PeriodSpending(_SpendingTotals):
    """Spending aggregation for a specific time period.

    Attributes:
        start: Period start (inclusive).
        end: Period end (exclusive).
    """

    start: datetime = Field(description="Period start (inclusive)")
    end: datetime = Field(description="Period end (exclusive)")

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


class AgentSpending(_SpendingTotals):
    """Spending aggregation for a single agent.

    Attributes:
        agent_id: Agent identifier.
    """

    agent_id: NotBlankStr = Field(description="Agent identifier")


class DepartmentSpending(_SpendingTotals):
    """Spending aggregation for a department.

    Attributes:
        department_name: Department name.
    """

    department_name: NotBlankStr = Field(
        description="Department name",
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
