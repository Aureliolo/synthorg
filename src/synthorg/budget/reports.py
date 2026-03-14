"""CFO spending report generation.

Provides multi-dimensional spending reports with breakdowns by task,
provider, model, and time-period comparison. Composes
:class:`~synthorg.budget.tracker.CostTracker` and
:class:`~synthorg.budget.config.BudgetConfig`.

Service layer backing CFO reporting (see Operations design page).
"""

import math
from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.budget.spending_summary import SpendingSummary  # noqa: TC001
from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.cfo import (
    CFO_REPORT_GENERATED,
    CFO_REPORT_GENERATOR_CREATED,
    CFO_REPORT_VALIDATION_ERROR,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from synthorg.budget.config import BudgetConfig
    from synthorg.budget.cost_record import CostRecord
    from synthorg.budget.tracker import CostTracker

logger = get_logger(__name__)


# ── Report Models ─────────────────────────────────────────────────


class TaskSpending(BaseModel):
    """Spending aggregation for a single task.

    Attributes:
        task_id: Task identifier.
        total_cost_usd: Total cost for the task.
        total_tokens: Total tokens consumed (input + output).
        record_count: Number of cost records.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    task_id: NotBlankStr = Field(description="Task identifier")
    total_cost_usd: float = Field(ge=0.0, description="Total cost")
    total_tokens: int = Field(ge=0, description="Total tokens consumed")
    record_count: int = Field(ge=0, description="Number of cost records")


class ProviderDistribution(BaseModel):
    """Cost distribution for a single provider.

    Attributes:
        provider: Provider name.
        total_cost_usd: Total cost for the provider.
        record_count: Number of cost records.
        percentage_of_total: Percentage of total spending.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    provider: NotBlankStr = Field(description="Provider name")
    total_cost_usd: float = Field(ge=0.0, description="Total cost")
    record_count: int = Field(ge=0, description="Number of cost records")
    percentage_of_total: float = Field(
        ge=0.0,
        le=100.0,
        description="Percentage of total spending",
    )


class ModelDistribution(BaseModel):
    """Cost distribution for a single model.

    Attributes:
        model: Model identifier.
        provider: Provider name.
        total_cost_usd: Total cost for the model.
        record_count: Number of cost records.
        percentage_of_total: Percentage of total spending.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    model: NotBlankStr = Field(description="Model identifier")
    provider: NotBlankStr = Field(description="Provider name")
    total_cost_usd: float = Field(ge=0.0, description="Total cost")
    record_count: int = Field(ge=0, description="Number of cost records")
    percentage_of_total: float = Field(
        ge=0.0,
        le=100.0,
        description="Percentage of total spending",
    )


class PeriodComparison(BaseModel):
    """Comparison of spending between two consecutive periods.

    Attributes:
        current_period_cost: Cost in the current period.
        previous_period_cost: Cost in the previous period.
        cost_change_usd: Absolute change in cost (computed).
        cost_change_percent: Percentage change in cost (computed).
            None when previous period cost is zero.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

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
    def cost_change_usd(self) -> float:
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
            self.cost_change_usd / self.previous_period_cost * 100,
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

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
        description="Top agents by cost (agent_id, cost_usd)",
    )
    top_tasks_by_cost: tuple[tuple[NotBlankStr, float], ...] = Field(
        default=(),
        description="Top tasks by cost (task_id, cost_usd)",
    )
    generated_at: datetime = Field(description="When the report was generated")

    @model_validator(mode="after")
    def _validate_agent_ranking_order(self) -> Self:
        """Ensure top_agents_by_cost is sorted descending."""
        costs = [c for _, c in self.top_agents_by_cost]
        if costs != sorted(costs, reverse=True):
            msg = "top_agents_by_cost must be sorted by cost descending"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_task_ranking_order(self) -> Self:
        """Ensure top_tasks_by_cost is sorted descending."""
        costs = [c for _, c in self.top_tasks_by_cost]
        if costs != sorted(costs, reverse=True):
            msg = "top_tasks_by_cost must be sorted by cost descending"
            raise ValueError(msg)
        return self


# ── ReportGenerator Service ───────────────────────────────────────


class ReportGenerator:
    """Generates multi-dimensional spending reports.

    Composes CostTracker and BudgetConfig to produce reports with
    breakdowns by task, provider, model, and period comparison.

    Args:
        cost_tracker: Cost tracking service for querying spend.
        budget_config: Budget configuration for context.
    """

    def __init__(
        self,
        *,
        cost_tracker: CostTracker,
        budget_config: BudgetConfig,
    ) -> None:
        self._cost_tracker = cost_tracker
        self._budget_config = budget_config
        logger.debug(
            CFO_REPORT_GENERATOR_CREATED,
            has_budget_config=True,
        )

    async def generate_report(
        self,
        *,
        start: datetime,
        end: datetime,
        top_n: int = 10,
        include_period_comparison: bool = True,
    ) -> SpendingReport:
        """Generate a spending report for the given period.

        Fetches records and summary concurrently; derives ``total_cost``
        from the records snapshot for consistent distribution
        percentages.

        Args:
            start: Inclusive period start.
            end: Exclusive period end.
            top_n: Maximum number of top agents/tasks to include.
            include_period_comparison: Whether to compute a comparison
                with the previous period of the same duration.

        Returns:
            Multi-dimensional spending report.

        Raises:
            ValueError: If ``start >= end`` or ``top_n < 1``.
        """
        if start >= end:
            logger.warning(
                CFO_REPORT_VALIDATION_ERROR,
                error="start_after_end",
                start=start.isoformat(),
                end=end.isoformat(),
            )
            msg = f"start ({start.isoformat()}) must be before end ({end.isoformat()})"
            raise ValueError(msg)
        if top_n < 1:
            logger.warning(
                CFO_REPORT_VALIDATION_ERROR,
                error="top_n_below_minimum",
                top_n=top_n,
            )
            msg = f"top_n must be >= 1, got {top_n}"
            raise ValueError(msg)

        now = datetime.now(UTC)

        records = await self._cost_tracker.get_records(
            start=start,
            end=end,
        )
        summary = await self._cost_tracker.build_summary(
            start=start,
            end=end,
        )

        # Derive total_cost from records for consistent percentages
        total_cost = round(
            math.fsum(r.cost_usd for r in records),
            BUDGET_ROUNDING_PRECISION,
        )
        by_task = _build_task_spendings(records)
        by_provider = _build_provider_distribution(records, total_cost)
        by_model = _build_model_distribution(records, total_cost)

        top_agents = _build_top_agents(summary, top_n)
        top_tasks = _build_top_tasks(by_task, top_n)

        period_comparison: PeriodComparison | None = None
        if include_period_comparison:
            period_comparison = await self._build_period_comparison(
                start,
                end,
                total_cost,
            )

        report = SpendingReport(
            summary=summary,
            by_task=by_task,
            by_provider=by_provider,
            by_model=by_model,
            period_comparison=period_comparison,
            top_agents_by_cost=top_agents,
            top_tasks_by_cost=top_tasks,
            generated_at=now,
        )

        logger.info(
            CFO_REPORT_GENERATED,
            total_cost_usd=total_cost,
            task_count=len(by_task),
            provider_count=len(by_provider),
            model_count=len(by_model),
            has_comparison=period_comparison is not None,
        )

        return report

    async def _build_period_comparison(
        self,
        current_start: datetime,
        current_end: datetime,
        current_cost: float,
    ) -> PeriodComparison | None:
        """Build a period comparison with the previous period."""
        duration = current_end - current_start
        prev_start = current_start - duration
        prev_end = current_start

        prev_summary = await self._cost_tracker.build_summary(
            start=prev_start,
            end=prev_end,
        )
        prev_cost = prev_summary.period.total_cost_usd

        if prev_cost == 0.0 and current_cost == 0.0:
            return None

        return PeriodComparison(
            current_period_cost=current_cost,
            previous_period_cost=prev_cost,
        )


# ── Module-level pure helpers ────────────────────────────────────


def _build_task_spendings(
    records: Sequence[CostRecord],
) -> tuple[TaskSpending, ...]:
    """Group records by task and aggregate."""
    by_task: dict[str, list[CostRecord]] = defaultdict(list)
    for r in records:
        by_task[r.task_id].append(r)

    spendings: list[TaskSpending] = []
    for task_id in sorted(by_task):
        task_records = by_task[task_id]
        total_cost = round(
            math.fsum(r.cost_usd for r in task_records),
            BUDGET_ROUNDING_PRECISION,
        )
        total_tokens = sum(r.input_tokens + r.output_tokens for r in task_records)
        spendings.append(
            TaskSpending(
                task_id=task_id,
                total_cost_usd=total_cost,
                total_tokens=total_tokens,
                record_count=len(task_records),
            ),
        )
    return tuple(spendings)


def _build_provider_distribution(
    records: Sequence[CostRecord],
    total_cost: float,
) -> tuple[ProviderDistribution, ...]:
    """Group records by provider and compute distribution."""
    by_provider: dict[str, list[CostRecord]] = defaultdict(list)
    for r in records:
        by_provider[r.provider].append(r)

    distributions: list[ProviderDistribution] = []
    for provider in sorted(by_provider):
        provider_records = by_provider[provider]
        provider_cost = round(
            math.fsum(r.cost_usd for r in provider_records),
            BUDGET_ROUNDING_PRECISION,
        )
        pct = (
            round(provider_cost / total_cost * 100, BUDGET_ROUNDING_PRECISION)
            if total_cost > 0
            else 0.0
        )
        distributions.append(
            ProviderDistribution(
                provider=provider,
                total_cost_usd=provider_cost,
                record_count=len(provider_records),
                percentage_of_total=pct,
            ),
        )
    return tuple(distributions)


def _build_model_distribution(
    records: Sequence[CostRecord],
    total_cost: float,
) -> tuple[ModelDistribution, ...]:
    """Group records by (model, provider) and compute distribution."""
    by_model: dict[tuple[str, str], list[CostRecord]] = defaultdict(list)
    for r in records:
        by_model[(r.model, r.provider)].append(r)

    distributions: list[ModelDistribution] = []
    for model, provider in sorted(by_model):
        model_records = by_model[(model, provider)]
        model_cost = round(
            math.fsum(r.cost_usd for r in model_records),
            BUDGET_ROUNDING_PRECISION,
        )
        pct = (
            round(model_cost / total_cost * 100, BUDGET_ROUNDING_PRECISION)
            if total_cost > 0
            else 0.0
        )
        distributions.append(
            ModelDistribution(
                model=model,
                provider=provider,
                total_cost_usd=model_cost,
                record_count=len(model_records),
                percentage_of_total=pct,
            ),
        )
    return tuple(distributions)


def _build_top_agents(
    summary: SpendingSummary,
    top_n: int,
) -> tuple[tuple[str, float], ...]:
    """Extract top-N agents by cost from a spending summary."""
    sorted_agents = sorted(
        summary.by_agent,
        key=lambda a: a.total_cost_usd,
        reverse=True,
    )
    return tuple((a.agent_id, a.total_cost_usd) for a in sorted_agents[:top_n])


def _build_top_tasks(
    task_spendings: tuple[TaskSpending, ...],
    top_n: int,
) -> tuple[tuple[str, float], ...]:
    """Extract top-N tasks by cost from task spendings."""
    sorted_tasks = sorted(
        task_spendings,
        key=lambda t: t.total_cost_usd,
        reverse=True,
    )
    return tuple((t.task_id, t.total_cost_usd) for t in sorted_tasks[:top_n])
