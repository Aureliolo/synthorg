"""Pydantic models for CFO spending reports.

Multi-dimensional report shapes (per-task, per-provider, per-model, and
period comparison) consumed by
:class:`~synthorg.budget.reports.ReportGenerator`.
"""

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.budget.currency import CurrencyCode
from synthorg.budget.spending_summary import SpendingSummary
from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.core.types import NotBlankStr


class TaskSpending(BaseModel):
    """Spending aggregation for a single task.

    Attributes:
        task_id: Task identifier.
        total_cost: Total cost for the task.
        currency: ISO 4217 currency code shared by every contributing
            record; ``None`` only when ``record_count == 0``.
        total_tokens: Total tokens consumed (input + output).
        record_count: Number of cost records.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task_id: NotBlankStr = Field(description="Task identifier")
    total_cost: float = Field(ge=0.0, description="Total cost")
    currency: CurrencyCode | None = Field(
        default=None,
        description="Currency shared by every contributing record",
    )
    total_tokens: int = Field(ge=0, description="Total tokens consumed")
    record_count: int = Field(ge=0, description="Number of cost records")

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


class ProviderDistribution(BaseModel):
    """Cost distribution for a single provider.

    Attributes:
        provider: Provider name.
        total_cost: Total cost for the provider.
        currency: ISO 4217 currency code shared by every contributing
            record; ``None`` only when ``record_count == 0``.
        record_count: Number of cost records.
        percentage_of_total: Percentage of total spending.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: NotBlankStr = Field(description="Provider name")
    total_cost: float = Field(ge=0.0, description="Total cost")
    currency: CurrencyCode | None = Field(
        default=None,
        description="Currency shared by every contributing record",
    )
    record_count: int = Field(ge=0, description="Number of cost records")
    percentage_of_total: float = Field(
        ge=0.0,
        le=100.0,
        description="Percentage of total spending",
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


class ModelDistribution(BaseModel):
    """Cost distribution for a single model.

    Attributes:
        model: Model identifier.
        provider: Provider name.
        total_cost: Total cost for the model.
        currency: ISO 4217 currency code shared by every contributing
            record; ``None`` only when ``record_count == 0``.
        record_count: Number of cost records.
        percentage_of_total: Percentage of total spending.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    model: NotBlankStr = Field(description="Model identifier")
    provider: NotBlankStr = Field(description="Provider name")
    total_cost: float = Field(ge=0.0, description="Total cost")
    currency: CurrencyCode | None = Field(
        default=None,
        description="Currency shared by every contributing record",
    )
    record_count: int = Field(ge=0, description="Number of cost records")
    percentage_of_total: float = Field(
        ge=0.0,
        le=100.0,
        description="Percentage of total spending",
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


class PeriodComparison(BaseModel):
    """Comparison of spending between two consecutive periods.

    Attributes:
        current_period_cost: Cost in the current period.
        previous_period_cost: Cost in the previous period.
        cost_change: Absolute change in cost (computed).
        cost_change_percent: Percentage change in cost (computed).
            None when previous period cost is zero.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    current_period_cost: float = Field(
        ge=0.0,
        description="Current period cost",
    )
    previous_period_cost: float = Field(
        ge=0.0,
        description="Previous period cost",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cost_change(self) -> float:
        """Absolute cost change (current - previous)."""
        return round(
            self.current_period_cost - self.previous_period_cost,
            BUDGET_ROUNDING_PRECISION,
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cost_change_percent(self) -> float | None:
        """Percentage cost change. None when previous period cost is zero."""
        if self.previous_period_cost <= 0:
            return None
        return round(
            self.cost_change / self.previous_period_cost * 100,
            BUDGET_ROUNDING_PRECISION,
        )


class SpendingReport(BaseModel):
    """Multi-dimensional spending report.

    Attributes:
        summary: Overall spending summary for the period.
        by_task: Per-task spending breakdown.
        by_provider: Per-provider cost distribution.
        by_model: Per-model cost distribution.
        period_comparison: Comparison with previous period (optional).
        top_agents_by_cost: Top agents by cost (sorted descending).
        top_tasks_by_cost: Top tasks by cost (sorted descending).
        generated_at: When the report was generated.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    summary: SpendingSummary = Field(description="Overall spending summary")
    by_task: tuple[TaskSpending, ...] = Field(
        default=(),
        description="Per-task spending breakdown",
    )
    by_provider: tuple[ProviderDistribution, ...] = Field(
        default=(),
        description="Per-provider cost distribution",
    )
    by_model: tuple[ModelDistribution, ...] = Field(
        default=(),
        description="Per-model cost distribution",
    )
    period_comparison: PeriodComparison | None = Field(
        default=None,
        description="Comparison with previous period",
    )
    top_agents_by_cost: tuple[tuple[NotBlankStr, float], ...] = Field(
        default=(),
        description="Top agents by cost (agent_id, cost)",
    )
    top_tasks_by_cost: tuple[tuple[NotBlankStr, float], ...] = Field(
        default=(),
        description="Top tasks by cost (task_id, cost)",
    )
    generated_at: datetime = Field(description="When the report was generated")

    @model_validator(mode="after")
    def _validate_agent_ranking_order(self) -> Self:
        """Ensure top_agents_by_cost is sorted descending.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        costs = [c for _, c in self.top_agents_by_cost]
        if costs != sorted(costs, reverse=True):
            msg = "top_agents_by_cost must be sorted by cost descending"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_task_ranking_order(self) -> Self:
        """Ensure top_tasks_by_cost is sorted descending.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        costs = [c for _, c in self.top_tasks_by_cost]
        if costs != sorted(costs, reverse=True):
            msg = "top_tasks_by_cost must be sorted by cost descending"
            raise ValueError(msg)
        return self
