"""CFO spending report generation.

Provides multi-dimensional spending reports with breakdowns by task,
provider, model, and time-period comparison. Composes
:class:`~synthorg.budget.tracker.CostTracker` and
:class:`~synthorg.budget.config.BudgetConfig`.

Service layer backing CFO reporting (see Operations design page).
"""

import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Final

from synthorg.budget._aggregation import sum_tokens
from synthorg.budget.config import BudgetConfig
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import assert_currencies_match
from synthorg.budget.report_models import (
    ModelDistribution,
    PeriodComparison,
    ProviderDistribution,
    SpendingReport,
    TaskSpending,
)
from synthorg.budget.spending_summary import SpendingSummary
from synthorg.budget.tracker import CostTracker
from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.observability import get_logger
from synthorg.observability.events.cfo import (
    CFO_REPORT_GENERATED,
    CFO_REPORT_GENERATOR_CREATED,
    CFO_REPORT_VALIDATION_ERROR,
)

logger = get_logger(__name__)

_DEFAULT_TOP_N: Final[int] = 10


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
        top_n: int = _DEFAULT_TOP_N,
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
        self._cost_tracker._log_retention_window(start)  # noqa: SLF001
        summary = self._cost_tracker.build_summary_from_records(
            records,
            start=start,
            end=end,
        )

        # Derive total_cost from the same snapshot the summary used so
        # the percentages, period totals, and breakdowns are guaranteed
        # to agree (a second tracker read here could race a concurrent
        # ``record()`` and produce inconsistent rollups).
        assert_currencies_match(r.currency for r in records)
        total_cost = round(
            math.fsum(r.cost for r in records),
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
            total_cost=total_cost,
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
        """Build a period comparison with the previous period.

        Returns:
            The resulting ``PeriodComparison``, or ``None`` when unavailable.
        """
        duration = current_end - current_start
        prev_start = current_start - duration
        prev_end = current_start

        prev_summary = await self._cost_tracker.build_summary(
            start=prev_start,
            end=prev_end,
        )
        prev_cost = prev_summary.period.total_cost

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
    """Group records by task and aggregate.

    Returns:
        Tuple of ``TaskSpending``.
    """
    by_task: dict[str, list[CostRecord]] = defaultdict(list)
    for r in records:
        by_task[r.task_id].append(r)

    spendings: list[TaskSpending] = []
    for task_id in sorted(by_task):
        task_records = by_task[task_id]
        task_currency = assert_currencies_match(
            (r.currency for r in task_records),
            task_id=task_id,
        )
        total_cost = round(
            math.fsum(r.cost for r in task_records),
            BUDGET_ROUNDING_PRECISION,
        )
        total_tokens = sum_tokens(task_records)
        spendings.append(
            TaskSpending(
                task_id=task_id,
                total_cost=total_cost,
                currency=task_currency,
                total_tokens=total_tokens,
                record_count=len(task_records),
            ),
        )
    return tuple(spendings)


def _build_provider_distribution(
    records: Sequence[CostRecord],
    total_cost: float,
) -> tuple[ProviderDistribution, ...]:
    """Group records by provider and compute distribution.

    Returns:
        Tuple of ``ProviderDistribution``.
    """
    by_provider: dict[str, list[CostRecord]] = defaultdict(list)
    for r in records:
        by_provider[r.provider].append(r)

    distributions: list[ProviderDistribution] = []
    for provider in sorted(by_provider):
        provider_records = by_provider[provider]
        provider_currency = assert_currencies_match(
            r.currency for r in provider_records
        )
        provider_cost = round(
            math.fsum(r.cost for r in provider_records),
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
                total_cost=provider_cost,
                currency=provider_currency,
                record_count=len(provider_records),
                percentage_of_total=pct,
            ),
        )
    return tuple(distributions)


def _build_model_distribution(
    records: Sequence[CostRecord],
    total_cost: float,
) -> tuple[ModelDistribution, ...]:
    """Group records by (model, provider) and compute distribution.

    Returns:
        Tuple of ``ModelDistribution``.
    """
    by_model: dict[tuple[str, str], list[CostRecord]] = defaultdict(list)
    for r in records:
        by_model[(r.model, r.provider)].append(r)

    distributions: list[ModelDistribution] = []
    for model, provider in sorted(by_model):
        model_records = by_model[(model, provider)]
        model_currency = assert_currencies_match(r.currency for r in model_records)
        model_cost = round(
            math.fsum(r.cost for r in model_records),
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
                total_cost=model_cost,
                currency=model_currency,
                record_count=len(model_records),
                percentage_of_total=pct,
            ),
        )
    return tuple(distributions)


def _build_top_agents(
    summary: SpendingSummary,
    top_n: int,
) -> tuple[tuple[str, float], ...]:
    """Extract top-N agents by cost from a spending summary.

    Returns:
        Tuple of ``tuple[str, float]``.
    """
    sorted_agents = sorted(
        summary.by_agent,
        key=lambda a: a.total_cost,
        reverse=True,
    )
    return tuple((a.agent_id, a.total_cost) for a in sorted_agents[:top_n])


def _build_top_tasks(
    task_spendings: tuple[TaskSpending, ...],
    top_n: int,
) -> tuple[tuple[str, float], ...]:
    """Extract top-N tasks by cost from task spendings.

    Returns:
        Tuple of ``tuple[str, float]``.
    """
    sorted_tasks = sorted(
        task_spendings,
        key=lambda t: t.total_cost,
        reverse=True,
    )
    return tuple((t.task_id, t.total_cost) for t in sorted_tasks[:top_n])
