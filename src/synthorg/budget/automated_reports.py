"""Automated report generation service.

Composes existing trackers (cost, risk, performance) and the
CFO ``ReportGenerator`` to produce periodic comprehensive reports.

Service layer for the Automated Reporting section of the Operations
design page.
"""

import asyncio
import datetime as _dt
import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from synthorg.budget.report_config import ReportPeriod
from synthorg.budget.report_templates import (
    AgentPerformanceSummary,
    ComprehensiveReport,
    DailyRiskPoint,
    PerformanceMetricsReport,
    RiskTrendsReport,
    TaskCompletionReport,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.hr.performance.models import TaskMetricRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.reporting import (
    REPORTING_GENERATION_COMPLETED,
    REPORTING_GENERATION_FAILED,
    REPORTING_GENERATION_STARTED,
    REPORTING_PERIOD_COMPUTED,
    REPORTING_SERVICE_CREATED,
)

if TYPE_CHECKING:
    from synthorg.budget.cost_record import CostRecord
    from synthorg.budget.report_config import AutomatedReportingConfig
    from synthorg.budget.reports import ReportGenerator, SpendingReport
    from synthorg.budget.risk_record import RiskRecord
    from synthorg.budget.risk_tracker import RiskTracker
    from synthorg.budget.tracker import CostTracker
    from synthorg.hr.performance.tracker import PerformanceTracker

logger = get_logger(__name__)
_DEFAULT_TOP_N: Final[int] = 10


class AutomatedReportService:
    """Generates comprehensive periodic reports.

    Composes existing services to produce reports covering spending,
    performance, task completion, and risk trends.

    Args:
        report_generator: CFO spending report generator.
        cost_tracker: Cost tracking service.
        risk_tracker: Optional risk tracking service.
        performance_tracker: Optional performance tracking service.
        config: Optional automated reporting configuration.
    """

    def __init__(
        self,
        *,
        report_generator: ReportGenerator,
        cost_tracker: CostTracker,
        risk_tracker: RiskTracker | None = None,
        performance_tracker: PerformanceTracker | None = None,
        config: AutomatedReportingConfig | None = None,
    ) -> None:
        self._report_generator = report_generator
        self._cost_tracker = cost_tracker
        self._risk_tracker = risk_tracker
        self._performance_tracker = performance_tracker
        self._config = config
        logger.debug(
            REPORTING_SERVICE_CREATED,
            has_risk_tracker=risk_tracker is not None,
            has_performance_tracker=performance_tracker is not None,
        )

    async def generate_spending_report(
        self,
        *,
        start: datetime,
        end: datetime,
        top_n: int = _DEFAULT_TOP_N,
    ) -> SpendingReport:
        """Generate a spending report. Delegates to ``ReportGenerator``.

        Returns:
            Result of type ``SpendingReport``.
        """
        return await self._report_generator.generate_report(
            start=start,
            end=end,
            top_n=top_n,
        )

    async def generate_performance_report(
        self,
        *,
        start: datetime,
        end: datetime,
    ) -> PerformanceMetricsReport:
        """Generate a performance metrics report.

        Returns an empty report when no performance tracker is available.

        Returns:
            Result of type ``PerformanceMetricsReport``.
        """
        now = datetime.now(UTC)
        if self._performance_tracker is None:
            return PerformanceMetricsReport(generated_at=now)

        metrics = self._performance_tracker.get_task_metrics(
            since=start,
            until=end,
        )
        cost_records = await self._cost_tracker.get_records(
            start=start,
            end=end,
        )
        risk_records = (
            await self._risk_tracker.get_records(
                start=start,
                end=end,
            )
            if self._risk_tracker is not None
            else ()
        )
        return _build_performance_report(
            metrics,
            cost_records,
            risk_records,
            now,
        )

    async def generate_task_completion_report(
        self,
        *,
        start: datetime,
        end: datetime,
    ) -> TaskCompletionReport:
        """Generate a task completion report.

        Prefers ``PerformanceTracker`` metrics when available for
        accurate success/failure counts. Falls back to a cost-record
        heuristic where each unique task_id is counted as assigned
        and completed.

        Returns:
            Result of type ``TaskCompletionReport``.
        """
        now = datetime.now(UTC)
        if self._performance_tracker is not None:
            metrics = self._performance_tracker.get_task_metrics(
                since=start,
                until=end,
            )
            completed = sum(1 for m in metrics if m.is_success)
            failed = sum(1 for m in metrics if not m.is_success)
            return TaskCompletionReport(
                total_assigned=len(metrics),
                total_completed=completed,
                total_failed=failed,
                generated_at=now,
            )
        # Fallback: cost records imply the task ran.
        records = await self._cost_tracker.get_records(
            start=start,
            end=end,
        )
        task_ids = {r.task_id for r in records}
        return TaskCompletionReport(
            total_assigned=len(task_ids),
            total_completed=len(task_ids),
            generated_at=now,
        )

    async def generate_risk_trends_report(
        self,
        *,
        start: datetime,
        end: datetime,
    ) -> RiskTrendsReport:
        """Generate a risk trends report.

        Returns an empty report when no risk tracker is available.

        Returns:
            Result of type ``RiskTrendsReport``.
        """
        now = datetime.now(UTC)
        if self._risk_tracker is None:
            return RiskTrendsReport(generated_at=now)

        records = await self._risk_tracker.get_records(
            start=start,
            end=end,
        )
        return _build_risk_trends_report(records, now)

    async def generate_comprehensive_report(
        self,
        *,
        period: ReportPeriod,
        reference_time: datetime | None = None,
    ) -> ComprehensiveReport:
        """Generate a comprehensive report for the given period.

        Args:
            period: The report period (daily/weekly/monthly).
            reference_time: Reference time for period computation.
                Defaults to current UTC time.

        Returns:
            Result of type ``ComprehensiveReport``.

        Raises:
            Exception: Raised when the relevant invariant fails.
        """
        ref = reference_time or datetime.now(UTC)

        try:
            start, end = compute_period_range(period, ref)
            now = datetime.now(UTC)

            logger.info(
                REPORTING_GENERATION_STARTED,
                period=period.value,
                start=start.isoformat(),
                end=end.isoformat(),
            )

            async with asyncio.TaskGroup() as tg:
                sp = tg.create_task(
                    self.generate_spending_report(start=start, end=end),
                )
                pf = tg.create_task(
                    self.generate_performance_report(start=start, end=end),
                )
                tc = tg.create_task(
                    self.generate_task_completion_report(start=start, end=end),
                )
                rt = tg.create_task(
                    self.generate_risk_trends_report(start=start, end=end),
                )
            spending = sp.result()
            performance = pf.result()
            task_completion = tc.result()
            risk_trends = rt.result()
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                REPORTING_GENERATION_FAILED,
                period=period.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

        logger.info(
            REPORTING_GENERATION_COMPLETED,
            period=period.value,
            has_spending=len(spending.by_task) > 0,
            has_performance=len(performance.agent_snapshots) > 0,
            has_task_completion=task_completion.total_assigned > 0,
            has_risk_trends=risk_trends.total_risk_units > 0,
        )

        return ComprehensiveReport(
            period=period,
            start=start,
            end=end,
            spending=spending,
            performance=performance,
            task_completion=task_completion,
            risk_trends=risk_trends,
            generated_at=now,
        )


# ── Period computation ───────────────────────────────────────────


def compute_period_range(
    period: ReportPeriod,
    reference: datetime,
) -> tuple[datetime, datetime]:
    """Compute the start and end times for a report period.

    Converts the reference to UTC before truncating to avoid
    silent timezone corruption.

    Args:
        period: The report period.
        reference: Reference time (must be timezone-aware).

    Returns:
        (start, end) tuple where start is inclusive, end exclusive.

    Raises:
        ValueError: If reference is naive (no timezone).
    """
    if reference.tzinfo is None:
        msg = "reference datetime must be timezone-aware"
        raise ValueError(msg)

    ref_utc = reference.astimezone(UTC)

    if period == ReportPeriod.DAILY:
        start, end = _daily_range(ref_utc)
    elif period == ReportPeriod.WEEKLY:
        start, end = _weekly_range(ref_utc)
    else:
        start, end = _monthly_range(ref_utc)

    logger.debug(
        REPORTING_PERIOD_COMPUTED,
        period=period.value,
        start=start.isoformat(),
        end=end.isoformat(),
    )
    return start, end


def _daily_range(ref: datetime) -> tuple[datetime, datetime]:
    """Previous day: 00:00 UTC to 00:00 UTC.

    Returns:
        Tuple ``(datetime, datetime)``.
    """
    today = ref.replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=1), today


def _weekly_range(ref: datetime) -> tuple[datetime, datetime]:
    """Previous week: Monday 00:00 UTC to Monday 00:00 UTC.

    Returns:
        Tuple ``(datetime, datetime)``.
    """
    today = ref.replace(hour=0, minute=0, second=0, microsecond=0)
    current_monday = today - timedelta(days=today.weekday())
    return current_monday - timedelta(weeks=1), current_monday


def _monthly_range(ref: datetime) -> tuple[datetime, datetime]:
    """Previous month: 1st 00:00 UTC to 1st 00:00 UTC.

    Returns:
        Tuple ``(datetime, datetime)``.
    """
    first_of_month = ref.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    if first_of_month.month == 1:
        start = first_of_month.replace(
            year=first_of_month.year - 1,
            month=12,
        )
    else:
        start = first_of_month.replace(
            month=first_of_month.month - 1,
        )
    return start, first_of_month


# ── Pure helpers ─────────────────────────────────────────────────


def _build_performance_report(
    metrics: tuple[TaskMetricRecord, ...],
    cost_records: tuple[CostRecord, ...],
    risk_records: tuple[RiskRecord, ...],
    now: datetime,
) -> PerformanceMetricsReport:
    """Build performance report from pre-fetched data.

    Returns:
        Result of type ``PerformanceMetricsReport``.
    """
    # Group metrics by agent.
    by_agent: dict[str, list[TaskMetricRecord]] = defaultdict(list)
    for m in metrics:
        by_agent[m.agent_id].append(m)

    # Pre-aggregate cost and risk per agent.
    cost_by_agent: dict[str, float] = defaultdict(float)
    for r in cost_records:
        cost_by_agent[r.agent_id] += r.cost
    risk_by_agent: dict[str, list[float]] = defaultdict(list)
    for rr in risk_records:
        risk_by_agent[rr.agent_id].append(rr.risk_units)

    snapshots: list[AgentPerformanceSummary] = []
    all_quality: list[float] = []

    for agent_id in sorted(by_agent):
        snap = _build_agent_snapshot(
            agent_id,
            by_agent[agent_id],
            cost_by_agent.get(agent_id, 0.0),
            risk_by_agent.get(agent_id, []),
        )
        snapshots.append(snap)
        # Collect per-task scores (not per-agent averages) so each
        # task contributes equally to the org-wide average.
        all_quality.extend(
            m.quality_score for m in by_agent[agent_id] if m.quality_score is not None
        )

    org_avg = (
        round(math.fsum(all_quality) / len(all_quality), 2) if all_quality else None
    )

    return PerformanceMetricsReport(
        agent_snapshots=tuple(snapshots),
        average_quality_score=org_avg,
        total_tasks_completed=sum(s.tasks_completed for s in snapshots),
        total_tasks_failed=sum(s.tasks_failed for s in snapshots),
        generated_at=now,
    )


def _build_agent_snapshot(
    agent_id: str,
    metrics: list[TaskMetricRecord],
    agent_cost: float,
    agent_risk_values: list[float],
) -> AgentPerformanceSummary:
    """Build a single agent's performance summary.

    Returns:
        Result of type ``AgentPerformanceSummary``.
    """
    completed = sum(1 for m in metrics if m.is_success)
    scores = [m.quality_score for m in metrics]
    valid = [s for s in scores if s is not None]
    avg_q = round(math.fsum(valid) / len(valid), 2) if valid else None
    return AgentPerformanceSummary(
        agent_id=agent_id,
        tasks_completed=completed,
        tasks_failed=len(metrics) - completed,
        average_quality_score=avg_q,
        total_cost=agent_cost,
        total_risk_units=math.fsum(agent_risk_values),
    )


def _build_risk_trends_report(
    records: tuple[RiskRecord, ...],
    now: datetime,
) -> RiskTrendsReport:
    """Build risk trends from pre-fetched records.

    Returns:
        Result of type ``RiskTrendsReport``.
    """
    total_risk = math.fsum(r.risk_units for r in records)

    # Per-agent aggregation using fsum.
    agent_lists: dict[str, list[float]] = defaultdict(list)
    for r in records:
        agent_lists[r.agent_id].append(r.risk_units)
    risk_by_agent = tuple(
        sorted(
            ((aid, math.fsum(vals)) for aid, vals in agent_lists.items()),
            key=lambda x: x[1],
            reverse=True,
        ),
    )

    # Per-action-type aggregation using fsum.
    action_lists: dict[str, list[float]] = defaultdict(list)
    for r in records:
        action_lists[r.action_type].append(r.risk_units)
    risk_by_action_type = tuple(
        sorted(
            ((at, math.fsum(vals)) for at, vals in action_lists.items()),
            key=lambda x: x[1],
            reverse=True,
        ),
    )

    # Daily trend -- key on UTC date to avoid timezone misplacement.
    daily: dict[_dt.date, list[RiskRecord]] = defaultdict(list)
    for r in records:
        daily[r.timestamp.astimezone(UTC).date()].append(r)
    daily_trend = tuple(
        DailyRiskPoint(
            date=day,
            total_risk_units=math.fsum(rec.risk_units for rec in day_records),
            record_count=len(day_records),
        )
        for day, day_records in sorted(daily.items())
    )

    return RiskTrendsReport(
        total_risk_units=total_risk,
        risk_by_agent=risk_by_agent,
        risk_by_action_type=risk_by_action_type,
        daily_risk_trend=daily_trend,
        generated_at=now,
    )
