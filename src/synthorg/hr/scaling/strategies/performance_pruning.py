"""Performance pruning strategy.

Wraps existing PruningPolicy implementations and coordinates
with the evolution system to defer pruning agents under active
adaptation.
"""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.enums import ScalingActionType, ScalingStrategyName
from synthorg.hr.scaling.models import ScalingContext, ScalingDecision, ScalingSignal
from synthorg.hr.scaling.signals.benchmark import BENCHMARK_REGRESSION_SIGNAL
from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_SCALING_STRATEGY_EVALUATED

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from synthorg.hr.performance.models import AgentPerformanceSnapshot
    from synthorg.hr.pruning.policy import PruningPolicy

    EvolutionChecker = Callable[[NotBlankStr], Awaitable[bool]]

logger = get_logger(__name__)

_NAME = NotBlankStr("performance_pruning")
_ACTION_TYPES = frozenset({ScalingActionType.PRUNE})


def _benchmark_regressing(context: ScalingContext) -> bool:
    """Return whether the benchmark regression signal is set in *context*.

    Returns:
        ``True`` when a ``benchmark_is_regression`` signal with a positive
        value is present (the golden benchmark's latest run backslid).
    """
    return any(
        signal.name == BENCHMARK_REGRESSION_SIGNAL and signal.value > 0.0
        for signal in context.benchmark_signals
    )


class PerformancePruningStrategy:
    """Wraps existing pruning policies as a scaling strategy.

    Evaluates each agent through the configured ``PruningPolicy``
    and emits PRUNE decisions for eligible agents.

    When ``defer_during_evolution`` is True, agents with recent
    evolution adaptations are skipped.

    Args:
        policy: Pruning policy to delegate to.
        evolution_checker: Optional async callable that returns True
            if an agent has recent evolution adaptations. Signature:
            ``async def(agent_id: NotBlankStr) -> bool``.
        defer_during_evolution: Whether to defer pruning during
            active evolution.
        defer_during_benchmark_regression: Whether to defer pruning when the
            golden benchmark's latest run regressed. A measured org-wide
            quality drop is the wrong moment to shed capacity, so the strategy
            holds the team until the benchmark recovers.
    """

    def __init__(
        self,
        *,
        policy: PruningPolicy,
        evolution_checker: EvolutionChecker | None = None,
        defer_during_evolution: bool = True,
        defer_during_benchmark_regression: bool = True,
    ) -> None:
        self._policy = policy
        self._evolution_checker = evolution_checker
        self._defer_during_evolution = defer_during_evolution
        self._defer_during_benchmark_regression = defer_during_benchmark_regression

    @property
    def name(self) -> NotBlankStr:
        """Strategy identifier."""
        return _NAME

    @property
    def action_types(self) -> frozenset[ScalingActionType]:
        """Action types this strategy can produce."""
        return _ACTION_TYPES

    async def evaluate(
        self,
        context: ScalingContext,
    ) -> tuple[ScalingDecision, ...]:
        """Evaluate agents through the pruning policy.

        Reads ``context.performance_snapshots`` for per-agent snapshot
        data. Returns empty when no snapshots are available.

        Args:
            context: Aggregated company state snapshot.

        Returns:
            PRUNE decisions for eligible agents.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
            CancelledError: If the related operation fails.
        """
        snapshots: Mapping[str, AgentPerformanceSnapshot] = (
            context.performance_snapshots
        )
        if not snapshots:
            return ()

        if self._defer_during_benchmark_regression and _benchmark_regressing(context):
            logger.info(
                HR_SCALING_STRATEGY_EVALUATED,
                strategy="performance_pruning",
                decisions=0,
                reason="deferred_benchmark_regression",
            )
            return ()

        now = datetime.now(UTC)
        decisions: list[ScalingDecision] = []
        agents_processed = 0
        agents_skipped = 0
        agents_deferred = 0
        agents_error = 0

        for agent_id in context.agent_ids:
            agent_key = str(agent_id)
            snapshot = snapshots.get(agent_key)
            if snapshot is None:
                agents_skipped += 1
                continue

            try:
                # Check evolution deferral.
                if self._defer_during_evolution and self._evolution_checker is not None:
                    is_adapting = await self._evolution_checker(agent_id)
                    if is_adapting:
                        agents_deferred += 1
                        logger.debug(
                            HR_SCALING_STRATEGY_EVALUATED,
                            strategy="performance_pruning",
                            agent_id=agent_key,
                            reason="deferred_evolution_active",
                        )
                        continue

                evaluation = await self._policy.evaluate(agent_id, snapshot)

                if evaluation.eligible:
                    # Build per-agent signals from the snapshot's
                    # quality trends. Fleet-wide aggregates in
                    # context.performance_signals are NOT per-agent
                    # evidence and must not be attached here.
                    agent_signals = tuple(
                        ScalingSignal(
                            name=NotBlankStr(f"agent_{trend.metric_name}"),
                            value=float(trend.slope),
                            source=NotBlankStr("performance"),
                            timestamp=now,
                        )
                        for trend in snapshot.trends
                    )
                    decisions.append(
                        ScalingDecision(
                            action_type=ScalingActionType.PRUNE,
                            source_strategy=(ScalingStrategyName.PERFORMANCE_PRUNING),
                            target_agent_id=agent_id,
                            rationale=NotBlankStr(
                                "; ".join(str(r) for r in evaluation.reasons)
                                or "performance below threshold"
                            ),
                            confidence=0.8,
                            signals=agent_signals,
                            created_at=now,
                        ),
                    )
                agents_processed += 1
            except MemoryError, RecursionError, asyncio.CancelledError:
                raise
            except Exception as exc:
                reraise_critical(exc)
                agents_error += 1
                logger.error(
                    HR_SCALING_STRATEGY_EVALUATED,
                    strategy="performance_pruning",
                    agent_id=agent_key,
                    action="per_agent_error",
                )
                continue

        logger.info(
            HR_SCALING_STRATEGY_EVALUATED,
            strategy="performance_pruning",
            decisions=len(decisions),
            agents_total=len(context.agent_ids),
            agents_processed=agents_processed,
            agents_skipped=agents_skipped,
            agents_deferred=agents_deferred,
            agents_error=agents_error,
        )
        return tuple(decisions)
