"""Org signal snapshot builder.

Assembles a complete OrgSignalSnapshot by running all signal
aggregators in parallel via asyncio.TaskGroup.
"""

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

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
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.meta import (
    META_SIGNAL_AGGREGATION_COMPLETED,
    META_SIGNAL_AGGREGATION_FAILED,
    META_SIGNAL_AGGREGATION_STARTED,
)

if TYPE_CHECKING:
    from synthorg.meta.signals.benchmark import BenchmarkSignalAggregator
    from synthorg.meta.signals.budget import BudgetSignalAggregator
    from synthorg.meta.signals.coordination import (
        CoordinationSignalAggregator,
    )
    from synthorg.meta.signals.errors import ErrorSignalAggregator
    from synthorg.meta.signals.evolution import (
        EvolutionSignalAggregator,
    )
    from synthorg.meta.signals.performance import (
        PerformanceSignalAggregator,
    )
    from synthorg.meta.signals.scaling import ScalingSignalAggregator
    from synthorg.meta.signals.telemetry import (
        TelemetrySignalAggregator,
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
        scaling: Scaling signal aggregator.
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
        scaling: ScalingSignalAggregator,
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

        results: dict[str, object] = {}

        async def _run(name: str, coro: object) -> None:
            """Run aggregator, store result on success."""
            try:
                results[name] = await coro  # type: ignore[misc]
            except Exception as exc:
                reraise_critical(exc)
                log_exception_redacted(
                    logger, META_SIGNAL_AGGREGATION_FAILED, exc, domain=name
                )

        async with asyncio.TaskGroup() as tg:
            _ = tg.create_task(
                _run(
                    "perf",
                    self._performance.aggregate(since=since, until=until),
                )
            )
            _ = tg.create_task(
                _run(
                    "budget",
                    self._budget.aggregate(since=since, until=until),
                )
            )
            _ = tg.create_task(
                _run(
                    "coord",
                    self._coordination.aggregate(since=since, until=until),
                )
            )
            _ = tg.create_task(
                _run(
                    "scale",
                    self._scaling.aggregate(since=since, until=until),
                )
            )
            _ = tg.create_task(
                _run(
                    "err",
                    self._errors.aggregate(since=since, until=until),
                )
            )
            _ = tg.create_task(
                _run(
                    "evo",
                    self._evolution.aggregate(since=since, until=until),
                )
            )
            _ = tg.create_task(
                _run(
                    "telem",
                    self._telemetry.aggregate(since=since, until=until),
                )
            )
            if self._benchmark is not None:
                _ = tg.create_task(
                    _run(
                        "bench",
                        self._benchmark.aggregate(since=since, until=until),
                    )
                )

        perf = results.get("perf", _EMPTY_PERFORMANCE)
        budget = results.get("budget", _EMPTY_BUDGET)
        coord = results.get("coord", _EMPTY_COORDINATION)
        scale = results.get("scale", _EMPTY_SCALING)
        err = results.get("err", _EMPTY_ERRORS)
        evo = results.get("evo", _EMPTY_EVOLUTION)
        telem = results.get("telem", _EMPTY_TELEMETRY)
        bench = results.get("bench", _EMPTY_BENCHMARK)

        snapshot = OrgSignalSnapshot(
            performance=perf,  # type: ignore[arg-type]
            budget=budget,  # type: ignore[arg-type]
            coordination=coord,  # type: ignore[arg-type]
            scaling=scale,  # type: ignore[arg-type]
            errors=err,  # type: ignore[arg-type]
            evolution=evo,  # type: ignore[arg-type]
            telemetry=telem,  # type: ignore[arg-type]
            benchmark=bench,  # type: ignore[arg-type]
        )

        logger.info(
            META_SIGNAL_AGGREGATION_COMPLETED,
            domain="all",
        )
        return snapshot
