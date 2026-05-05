"""Summary/breakdown mixin for :class:`CostTracker`.

Extracted from :mod:`synthorg.budget.tracker` to keep the main
module under the size limit.
"""

import math
from collections import defaultdict
from collections.abc import Sequence  # noqa: TC003 -- runtime type
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from synthorg.budget.call_category import OrchestrationAlertLevel
from synthorg.budget.category_analytics import (
    CategoryBreakdown,
    OrchestrationRatio,
    build_category_breakdown,
    compute_orchestration_ratio,
)
from synthorg.budget.coordination_config import (
    OrchestrationAlertThresholds,  # noqa: TC001
)
from synthorg.budget.currency import assert_currencies_match
from synthorg.budget.enums import BudgetAlertLevel
from synthorg.budget.spending_summary import (
    AgentSpending,
    DepartmentSpending,
    PeriodSpending,
    SpendingSummary,
)
from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.budget import (
    BUDGET_CATEGORY_BREAKDOWN_QUERIED,
    BUDGET_DEPARTMENT_RESOLVE_FAILED,
    BUDGET_ORCHESTRATION_RATIO_ALERT,
    BUDGET_ORCHESTRATION_RATIO_QUERIED,
    BUDGET_QUERY_EXCEEDS_RETENTION,
    BUDGET_SUMMARY_BUILT,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from synthorg.budget.config import BudgetConfig
    from synthorg.budget.cost_record import CostRecord
    from synthorg.core.clock import Clock

_COST_WINDOW_HOURS = 24 * 30

logger = get_logger(__name__)


class CostTrackerSummaryMixin:
    """Mixin providing summary, breakdown, and orchestration-ratio helpers."""

    _budget_config: BudgetConfig | None
    _department_resolver: Callable[[str], str | None] | None
    _clock: Clock

    async def _snapshot(self, *, now: datetime | None = None) -> tuple[CostRecord, ...]:
        """Return an immutable snapshot of records.

        Overridden by the concrete class; declared here only to satisfy
        mypy's attribute check against the mixin.
        """
        raise NotImplementedError

    async def build_summary(
        self,
        *,
        start: datetime,
        end: datetime,
    ) -> SpendingSummary:
        """Build a spending summary for the given period.

        Takes its own records snapshot.  Callers that already hold a
        snapshot (e.g. :class:`ReportGenerator`, which derives multiple
        breakdowns from one consistent view) should call
        :meth:`build_summary_from_records` directly with the pre-fetched
        records to avoid a second tracker read that could race against
        a concurrent ``record()``.
        """
        self._log_retention_window(start)
        snapshot = await self._snapshot()
        return self.build_summary_from_records(
            snapshot,
            start=start,
            end=end,
        )

    def build_summary_from_records(
        self,
        records: Sequence[CostRecord],
        *,
        start: datetime,
        end: datetime,
    ) -> SpendingSummary:
        """Build a spending summary from a pre-fetched records snapshot.

        Synchronous and stateless: every aggregation derives from
        *records* alone, so callers can produce a summary plus
        breakdowns from the same atomic view of the tracker.
        """
        from synthorg.budget._tracker_helpers import (  # noqa: PLC0415
            _aggregate,
            _build_agent_spendings,
            _filter_records,
            _validate_time_range,
        )

        _validate_time_range(start, end)
        filtered = _filter_records(records, start=start, end=end)
        totals = _aggregate(filtered)

        agent_spendings = _build_agent_spendings(filtered)
        dept_spendings = self._build_dept_spendings(agent_spendings)
        budget_monthly, used_pct, alert = self._build_budget_context(
            totals.cost,
        )

        summary = SpendingSummary(
            period=PeriodSpending(
                start=start,
                end=end,
                total_cost=totals.cost,
                currency=totals.currency,
                total_input_tokens=totals.input_tokens,
                total_output_tokens=totals.output_tokens,
                record_count=totals.record_count,
            ),
            by_agent=tuple(agent_spendings),
            by_department=tuple(dept_spendings),
            budget_total_monthly=budget_monthly,
            budget_used_percent=used_pct,
            alert_level=alert,
        )

        logger.info(
            BUDGET_SUMMARY_BUILT,
            total_cost=totals.cost,
            record_count=totals.record_count,
            agent_count=len(agent_spendings),
            department_count=len(dept_spendings),
            alert_level=alert.value,
        )

        return summary

    def _log_retention_window(self, start: datetime) -> None:
        """Emit a warning if *start* predates the rolling retention cutoff.

        Reads ``now`` through the injected :class:`Clock` so tests can
        pin the cutoff with ``FakeClock`` and the warning's emission
        timing stays deterministic.
        """
        retention_cutoff = self._clock.now() - timedelta(
            hours=_COST_WINDOW_HOURS,
        )
        if start < retention_cutoff:
            logger.warning(
                BUDGET_QUERY_EXCEEDS_RETENTION,
                requested_start=start.isoformat(),
                retention_cutoff=retention_cutoff.isoformat(),
                retention_hours=_COST_WINDOW_HOURS,
            )

    async def get_category_breakdown(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> CategoryBreakdown:
        """Build a per-category cost breakdown."""
        from synthorg.budget._tracker_helpers import (  # noqa: PLC0415
            _filter_records,
            _validate_time_range,
        )

        _validate_time_range(start, end)
        logger.debug(
            BUDGET_CATEGORY_BREAKDOWN_QUERIED,
            agent_id=agent_id,
            task_id=task_id,
            start=start,
            end=end,
        )
        snapshot = await self._snapshot()
        filtered = _filter_records(
            snapshot,
            agent_id=agent_id,
            task_id=task_id,
            start=start,
            end=end,
        )
        return build_category_breakdown(filtered)

    async def get_orchestration_ratio(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        thresholds: OrchestrationAlertThresholds | None = None,
    ) -> OrchestrationRatio:
        """Compute the orchestration overhead ratio."""
        breakdown = await self.get_category_breakdown(
            agent_id=agent_id,
            task_id=task_id,
            start=start,
            end=end,
        )
        result = compute_orchestration_ratio(
            breakdown,
            thresholds=thresholds,
        )
        logger.debug(
            BUDGET_ORCHESTRATION_RATIO_QUERIED,
            agent_id=agent_id,
            task_id=task_id,
            ratio=result.ratio,
            alert_level=result.alert_level.value,
        )
        if result.alert_level != OrchestrationAlertLevel.NORMAL:
            logger.warning(
                BUDGET_ORCHESTRATION_RATIO_ALERT,
                agent_id=agent_id,
                task_id=task_id,
                ratio=result.ratio,
                alert_level=result.alert_level.value,
            )
        return result

    def _build_dept_spendings(
        self,
        agent_spendings: list[AgentSpending],
    ) -> list[DepartmentSpending]:
        """Aggregate per-department spending from agent spendings."""
        dept_map: dict[str, list[AgentSpending]] = defaultdict(list)
        for agent_spend in agent_spendings:
            dept = self._resolve_department(agent_spend.agent_id)
            if dept is not None:
                dept_map[dept].append(agent_spend)

        results: list[DepartmentSpending] = []
        for dname, spends in sorted(dept_map.items()):
            dept_currency = assert_currencies_match(s.currency for s in spends)
            results.append(
                DepartmentSpending(
                    department_name=dname,
                    total_cost=round(
                        math.fsum(s.total_cost for s in spends),
                        BUDGET_ROUNDING_PRECISION,
                    ),
                    currency=dept_currency,
                    total_input_tokens=sum(s.total_input_tokens for s in spends),
                    total_output_tokens=sum(s.total_output_tokens for s in spends),
                    record_count=sum(s.record_count for s in spends),
                )
            )
        return results

    def _build_budget_context(
        self,
        total_cost: float,
    ) -> tuple[float, float, BudgetAlertLevel]:
        """Compute budget monthly, used percentage, and alert level."""
        budget_monthly = (
            self._budget_config.total_monthly if self._budget_config else 0.0
        )
        used_pct = (
            round(
                total_cost / budget_monthly * 100,
                BUDGET_ROUNDING_PRECISION,
            )
            if budget_monthly > 0
            else 0.0
        )
        alert = self._compute_alert_level(used_pct)
        return budget_monthly, used_pct, alert

    def _compute_alert_level(self, used_pct: float) -> BudgetAlertLevel:
        """Determine alert level from the rounded budget percentage."""
        if self._budget_config is None or self._budget_config.total_monthly <= 0:
            return BudgetAlertLevel.NORMAL

        alerts = self._budget_config.alerts

        if used_pct >= alerts.hard_stop_at:
            return BudgetAlertLevel.HARD_STOP
        if used_pct >= alerts.critical_at:
            return BudgetAlertLevel.CRITICAL
        if used_pct >= alerts.warn_at:
            return BudgetAlertLevel.WARNING
        return BudgetAlertLevel.NORMAL

    def _resolve_department(self, agent_id: str) -> str | None:
        """Resolve agent to department, logging resolver errors."""
        if self._department_resolver is None:
            return None
        try:
            return self._department_resolver(agent_id)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                BUDGET_DEPARTMENT_RESOLVE_FAILED,
                agent_id=agent_id,
                error=safe_error_description(exc),
                error_type=type(exc).__qualname__,
            )
            return None
