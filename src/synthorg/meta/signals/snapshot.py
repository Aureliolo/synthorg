"""Org signal snapshot builder.

Assembles a complete OrgSignalSnapshot by running all signal
aggregators in parallel via asyncio.TaskGroup.
"""

import asyncio
from collections.abc import Awaitable
from datetime import UTC, datetime

from synthorg.core.critical_errors import reraise_critical
from synthorg.meta.signal_models import (
    OrgBenchmarkSummary,
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgScalingSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
)
from synthorg.meta.signals.benchmark import BenchmarkSignalAggregator
from synthorg.meta.signals.budget import BudgetSignalAggregator
from synthorg.meta.signals.coordination import CoordinationSignalAggregator
from synthorg.meta.signals.errors import ErrorSignalAggregator
from synthorg.meta.signals.evolution import EvolutionSignalAggregator
from synthorg.meta.signals.performance import PerformanceSignalAggregator
from synthorg.meta.signals.scaling import ScalingSignalAggregator
from synthorg.meta.signals.telemetry import TelemetrySignalAggregator
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.meta import (
    META_SIGNAL_AGGREGATION_COMPLETED,
    META_SIGNAL_AGGREGATION_FAILED,
    META_SIGNAL_AGGREGATION_STARTED,
)

logger = get_logger(__name__)

_EMPTY_PERFORMANCE = OrgPerformanceSummary(
    avg_quality_score=0.0,
    avg_success_rate=0.0,
    avg_collaboration_score=0.0,
    agent_count=0,
)
_EMPTY_BUDGET = OrgBudgetSummary(
    total_spend=0.0,
    productive_ratio=0.0,
    coordination_ratio=0.0,
    system_ratio=0.0,
    forecast_confidence=0.0,
    orchestration_overhead=0.0,
)
_EMPTY_COORDINATION = OrgCoordinationSummary()
_EMPTY_SCALING = OrgScalingSummary()
_EMPTY_ERRORS = OrgErrorSummary()
_EMPTY_EVOLUTION = OrgEvolutionSummary()
_EMPTY_TELEMETRY = OrgTelemetrySummary()
_EMPTY_BENCHMARK = OrgBenchmarkSummary()


class SnapshotBuilder:
    """Builds an OrgSignalSnapshot from all signal aggregators.

    Runs all aggregators in parallel using asyncio.TaskGroup.
    If an individual aggregator fails, a safe default is used
    for that domain without cancelling the others.

    Args:
        performance: Performance signal aggregator.
        budget: Budget signal aggregator.
        coordination: Coordination signal aggregator.
        scaling: Scaling signal aggregator, or ``None`` when no scaling
            service is wired (the snapshot then carries an empty scaling
            summary).
        errors: Error signal aggregator.
        evolution: Evolution signal aggregator.
        telemetry: Telemetry signal aggregator.
        benchmark: Golden-benchmark signal aggregator. Optional because
            the benchmark is an opt-in / offline signal; when ``None``
            the snapshot carries an empty benchmark summary.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        performance: PerformanceSignalAggregator,
        budget: BudgetSignalAggregator,
        coordination: CoordinationSignalAggregator,
        scaling: ScalingSignalAggregator | None,
        errors: ErrorSignalAggregator,
        evolution: EvolutionSignalAggregator,
        telemetry: TelemetrySignalAggregator,
        benchmark: BenchmarkSignalAggregator | None = None,
    ) -> None:
        self._performance = performance
        self._budget = budget
        self._coordination = coordination
        self._scaling = scaling
        self._errors = errors
        self._evolution = evolution
        self._telemetry = telemetry
        self._benchmark = benchmark

    async def build(
        self,
        *,
        since: datetime,
        until: datetime | None = None,
    ) -> OrgSignalSnapshot:
        """Build a complete org signal snapshot.

        Args:
            since: Start of observation window (UTC).
            until: End of observation window (defaults to now).

        Returns:
            Complete org signal snapshot.
        """
        if until is None:
            until = datetime.now(UTC)

        logger.info(
            META_SIGNAL_AGGREGATION_STARTED,
            since=since.isoformat(),
            until=until.isoformat(),
        )

        async def _safe[T](domain: str, coro: Awaitable[T], default: T) -> T:
            """Await *coro*, returning *default* if the aggregator fails.

            Returns:
                The aggregator result on success, else *default*.
            """
            try:
                return await coro
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                log_exception_redacted(
                    logger, META_SIGNAL_AGGREGATION_FAILED, exc, domain=domain
                )
                return default

        async with asyncio.TaskGroup() as tg:
            perf_task = tg.create_task(
                _safe(
                    "perf",
                    self._performance.aggregate(since=since, until=until),
                    _EMPTY_PERFORMANCE,
                )
            )
            budget_task = tg.create_task(
                _safe(
                    "budget",
                    self._budget.aggregate(since=since, until=until),
                    _EMPTY_BUDGET,
                )
            )
            coord_task = tg.create_task(
                _safe(
                    "coord",
                    self._coordination.aggregate(since=since, until=until),
                    _EMPTY_COORDINATION,
                )
            )
            scale_task = (
                tg.create_task(
                    _safe(
                        "scale",
                        self._scaling.aggregate(since=since, until=until),
                        _EMPTY_SCALING,
                    )
                )
                if self._scaling is not None
                else None
            )
            err_task = tg.create_task(
                _safe(
                    "err",
                    self._errors.aggregate(since=since, until=until),
                    _EMPTY_ERRORS,
                )
            )
            evo_task = tg.create_task(
                _safe(
                    "evo",
                    self._evolution.aggregate(since=since, until=until),
                    _EMPTY_EVOLUTION,
                )
            )
            telem_task = tg.create_task(
                _safe(
                    "telem",
                    self._telemetry.aggregate(since=since, until=until),
                    _EMPTY_TELEMETRY,
                )
            )
            bench_task = (
                tg.create_task(
                    _safe(
                        "bench",
                        self._benchmark.aggregate(since=since, until=until),
                        _EMPTY_BENCHMARK,
                    )
                )
                if self._benchmark is not None
                else None
            )

        snapshot = OrgSignalSnapshot(
            performance=perf_task.result(),
            budget=budget_task.result(),
            coordination=coord_task.result(),
            scaling=scale_task.result() if scale_task is not None else _EMPTY_SCALING,
            errors=err_task.result(),
            evolution=evo_task.result(),
            telemetry=telem_task.result(),
            benchmark=(
                bench_task.result() if bench_task is not None else _EMPTY_BENCHMARK
            ),
        )

        logger.info(
            META_SIGNAL_AGGREGATION_COMPLETED,
            domain="all",
        )
        return snapshot
