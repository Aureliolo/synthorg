"""Scaling context builder -- aggregates signals into a frozen context."""

import asyncio
import copy
from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from synthorg.core.critical_errors import reraise_critical
from synthorg.hr.scaling.models import ScalingContext, ScalingSignal
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import (
    HR_SCALING_CONTEXT_BUILT,
    HR_SCALING_SIGNAL_COLLECTION_DEGRADED,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.hr.scaling.protocols import ScalingSignalSource
    from synthorg.hr.scaling.signals.benchmark import BenchmarkSignalSource
    from synthorg.hr.scaling.signals.budget import BudgetSignalSource
    from synthorg.hr.scaling.signals.performance import (
        PerformanceSignalSource,
    )
    from synthorg.hr.scaling.signals.skill import SkillSignalSource
    from synthorg.hr.scaling.signals.workload import WorkloadSignalSource

logger = get_logger(__name__)


class ScalingContextBuilder:
    """Assembles a ``ScalingContext`` from signal sources.

    Each source is optional -- missing sources produce empty signal
    tuples in the context.

    Args:
        workload_source: Workload signal source.
        budget_source: Budget signal source.
        performance_source: Performance signal source.
        skill_source: Skill signal source.
        benchmark_source: Golden-benchmark signal source.
    """

    def __init__(
        self,
        *,
        workload_source: WorkloadSignalSource | None = None,
        budget_source: BudgetSignalSource | None = None,
        performance_source: PerformanceSignalSource | None = None,
        skill_source: SkillSignalSource | None = None,
        benchmark_source: BenchmarkSignalSource | None = None,
    ) -> None:
        self._workload = workload_source
        self._budget = budget_source
        self._performance = performance_source
        self._skill = skill_source
        self._benchmark = benchmark_source

    async def build(  # noqa: PLR0913 -- one kwargs slot per signal source
        self,
        *,
        agent_ids: tuple[NotBlankStr, ...],
        workload_kwargs: dict[str, Any] | None = None,
        budget_kwargs: dict[str, Any] | None = None,
        performance_kwargs: dict[str, Any] | None = None,
        skill_kwargs: dict[str, Any] | None = None,
        benchmark_kwargs: dict[str, Any] | None = None,
    ) -> ScalingContext:
        """Build a frozen scaling context from all signal sources.

        Args:
            agent_ids: IDs of all active agents.
            workload_kwargs: Extra kwargs for workload source.
            budget_kwargs: Extra kwargs for budget source.
            performance_kwargs: Extra kwargs for performance source.
            skill_kwargs: Extra kwargs for skill source.
            benchmark_kwargs: Extra kwargs for benchmark source.

        Returns:
            Frozen ``ScalingContext`` with all collected signals.
        """
        # Run all signal sources in parallel. _safe_collect already
        # catches exceptions per-source so a failure in one does not
        # cancel siblings.
        async with asyncio.TaskGroup() as tg:
            workload_task = tg.create_task(
                self._safe_collect(
                    "workload",
                    self._workload,
                    agent_ids,
                    workload_kwargs,
                ),
            )
            budget_task = tg.create_task(
                self._safe_collect(
                    "budget",
                    self._budget,
                    agent_ids,
                    budget_kwargs,
                ),
            )
            performance_task = tg.create_task(
                self._safe_collect(
                    "performance",
                    self._performance,
                    agent_ids,
                    performance_kwargs,
                ),
            )
            skill_task = tg.create_task(
                self._safe_collect(
                    "skill",
                    self._skill,
                    agent_ids,
                    skill_kwargs,
                ),
            )
            benchmark_task = tg.create_task(
                self._safe_collect(
                    "benchmark",
                    self._benchmark,
                    agent_ids,
                    benchmark_kwargs,
                ),
            )

        workload_signals = workload_task.result()
        budget_signals = budget_task.result()
        performance_signals = performance_task.result()
        skill_signals = skill_task.result()
        benchmark_signals = benchmark_task.result()

        raw_snapshots = (performance_kwargs or {}).get("snapshots", {})
        perf_snapshots = (
            MappingProxyType(copy.deepcopy(dict(raw_snapshots)))
            if isinstance(raw_snapshots, Mapping)
            else MappingProxyType({})
        )

        context = ScalingContext(
            agent_ids=agent_ids,
            workload_signals=workload_signals,
            budget_signals=budget_signals,
            performance_signals=performance_signals,
            skill_signals=skill_signals,
            benchmark_signals=benchmark_signals,
            performance_snapshots=perf_snapshots,
            evaluated_at=datetime.now(UTC),
        )
        logger.debug(
            HR_SCALING_CONTEXT_BUILT,
            agent_count=len(agent_ids),
            workload_signals=len(workload_signals),
            budget_signals=len(budget_signals),
            performance_signals=len(performance_signals),
            skill_signals=len(skill_signals),
            benchmark_signals=len(benchmark_signals),
        )
        return context

    @staticmethod
    async def _safe_collect(
        name: str,
        source: ScalingSignalSource | None,
        agent_ids: tuple[NotBlankStr, ...],
        kwargs: dict[str, Any] | None,
    ) -> tuple[ScalingSignal, ...]:
        """Collect signals from a source, degrading gracefully on failure.

        A single source crashing must not prevent the rest of the
        context from being built.

        Returns:
            Tuple of ``ScalingSignal``.

        Raises:
            CancelledError: If the related operation fails.
        """
        if source is None:
            return ()
        try:
            result: tuple[ScalingSignal, ...] = await source.collect(
                agent_ids,
                **(kwargs or {}),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                HR_SCALING_SIGNAL_COLLECTION_DEGRADED,
                source=name,
                action="collection_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ()
        return result
