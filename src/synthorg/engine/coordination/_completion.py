# module-kind: code
"""What happens once the waves are in: totals, then the side channels.

Two separable things, kept together because they share the same rule about
failure. Assembling the result is part of the run: a currency mismatch
across waves is a real defect and fails it. Everything after is not:
attribution, the performance tracker and the metrics collector all describe
a run that already finished, so a fault in any of them may not turn a
completed coordination into a failed one. Written inline they read as the
same kind of step, which is how a swallow ends up next to a raise with
nothing saying why they differ.
"""

from typing import TYPE_CHECKING

from synthorg.budget.currency import assert_currencies_match
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.coordination.attribution import (
    AgentContribution,
    build_agent_contributions,
)
from synthorg.engine.coordination.dispatcher_types import DispatchResult
from synthorg.engine.routing.models import RoutingResult
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.coordination import COORDINATION_CLEANUP_FAILED

if TYPE_CHECKING:
    # Cycle-breaker, as in the service: the tracker reaches back through the
    # HR package during a cold import.
    from synthorg.hr.performance.tracker import PerformanceTracker

logger = get_logger(__name__)


def aggregate_wave_cost(dispatch_result: DispatchResult) -> float:
    """Total what the completed waves spent, in one currency.

    Waves with no completed results report ``currency=None`` and
    ``total_cost=0``, so they are dropped before the guard: they cannot
    contribute to the aggregate, and passing ``None`` would fail closed
    under the missing-currency rule.

    Args:
        dispatch_result: The finished dispatch.

    Returns:
        The summed cost across every wave that completed.
    """
    wave_results = tuple(
        wave.execution_result
        for wave in dispatch_result.waves
        if wave.execution_result is not None
    )
    assert_currencies_match(
        result.currency for result in wave_results if result.currency is not None
    )
    return sum(result.total_cost for result in wave_results)


async def record_contributions(
    routing_result: RoutingResult,
    dispatch_result: DispatchResult,
    *,
    performance_tracker: PerformanceTracker | None,
    parent_task_id: str,
) -> tuple[AgentContribution, ...]:
    """Attribute the run to its agents, without ever failing it.

    Args:
        routing_result: Who was routed what.
        dispatch_result: What the waves produced.
        performance_tracker: The wired tracker, or ``None``.
        parent_task_id: Named on every diagnostic, so a swallowed fault is
            still attributable to the run it happened on.

    Returns:
        The per-agent contributions, or ``()`` when they could not be
        built. The run is over either way: an empty tuple is a lost
        measurement, not a failed coordination.
    """
    try:
        contributions = build_agent_contributions(
            routing_result,
            dispatch_result.waves,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort side channel
        reraise_critical(exc)
        logger.warning(
            COORDINATION_CLEANUP_FAILED,
            parent_task_id=parent_task_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            context="post_completion_attribution_build",
        )
        return ()

    if performance_tracker is None or not contributions:
        return contributions
    try:
        await performance_tracker.record_coordination_contributions(contributions)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort side channel
        reraise_critical(exc)
        logger.warning(
            COORDINATION_CLEANUP_FAILED,
            parent_task_id=parent_task_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            context="post_completion_tracker_write",
        )
    return contributions


__all__ = ["aggregate_wave_cost", "record_contributions"]
