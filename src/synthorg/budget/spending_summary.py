"""Spending summary models for aggregated cost reporting.

Provides the aggregation data structures used by
:class:`~synthorg.budget.tracker.CostTracker` for cost reporting and
designed for consumption by the CFO agent (see Operations design page).
Views of :class:`~synthorg.budget.cost_record.CostRecord` data are
aggregated by agent, department, and time period.
"""

from collections import Counter
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.budget.currency import CurrencyCode
from synthorg.budget.enums import BudgetAlertLevel
from synthorg.core.types import NotBlankStr


class _SpendingTotals(BaseModel):
    """Shared aggregation fields for spending summary models.

    Not intended for direct instantiation -- subclass with a
    dimension-specific identifier (agent, department, or period).

    Attributes:
        total_cost: Total cost for the aggregation group.
        currency: ISO 4217 currency code for ``total_cost``.  ``None``
            only when ``record_count == 0``; any non-empty aggregation
            carries the single currency shared by its contributing
            records (mixed-currency input raises
            :class:`~synthorg.budget.errors.MixedCurrencyAggregationError`
            at the aggregator).
        total_input_tokens: Total input tokens consumed.
        total_output_tokens: Total output tokens consumed.
        record_count: Number of cost records aggregated.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    total_cost: float = Field(
        default=0.0,
        ge=0.0,
        description="Total cost for the aggregation group",
    )
    currency: CurrencyCode | None = Field(
        default=None,
        description=(
            "ISO 4217 currency code for ``total_cost``; ``None`` only when "
            "``record_count == 0``"
        ),
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
    def _validate_currency_presence(self) -> Self:
        """Require ``currency`` whenever at least one record aggregated.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.record_count > 0 and self.currency is None:
            msg = (
                f"currency is required when record_count > 0 "
                f"(record_count={self.record_count})"
            )
            raise ValueError(msg)
        return self


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
        """Ensure start is strictly before end.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
        """Ensure no duplicate agent_id values in by_agent.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        ids = [a.agent_id for a in self.by_agent]
        if len(ids) != len(set(ids)):
            dupes = sorted(i for i, c in Counter(ids).items() if c > 1)
            msg = f"Duplicate agent_id values in by_agent: {dupes}"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_unique_department_names(self) -> Self:
        """Ensure no duplicate department_name values in by_department.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        names = [d.department_name for d in self.by_department]
        if len(names) != len(set(names)):
            dupes = sorted(n for n, c in Counter(names).items() if c > 1)
            msg = f"Duplicate department_name values in by_department: {dupes}"
            raise ValueError(msg)
        return self
