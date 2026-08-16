# module-kind: code
"""What a finished multi-agent run tells the coordination collector.

Aggregating a wave's sub-agent results into one payload is arithmetic over
the dispatch result and nothing else, so it lives beside the coordinator
rather than inside it. The recording half is here too because the two are
one decision: what to send, and that failing to send it must never fail a
run that already completed.
"""

from typing import Final

from synthorg.budget.coordination_collector import (
    CollectionInputs,
    CoordinationMetricsCollector,
    collect_bounded,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.coordination.dispatcher_types import DispatchResult
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.coordination import COORDINATION_CLEANUP_FAILED

logger = get_logger(__name__)

#: Multi-agent coordination has no single lead, so the payload's actor is
#: the system-level label rather than any one participant.
COORDINATOR_ACTOR: Final[str] = "coordinator"


async def collect_coordination_metrics(
    collector: CoordinationMetricsCollector | None,
    *,
    task_id: str,
    dispatch_result: DispatchResult,
) -> None:
    """Compute and record the multi-agent coordination metrics.

    Never fatal: a collector failure must not fail an already completed
    coordination run. Skipped when no collector is wired or no sub-agent
    produced a result.

    Args:
        collector: The wired collector, or ``None``.
        task_id: The parent task the run belonged to.
        dispatch_result: What the waves produced.
    """
    if collector is None:
        return
    inputs = build_collection_inputs(task_id, dispatch_result)
    if inputs is None:
        return
    try:
        await collect_bounded(collector, inputs)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort metrics
        reraise_critical(exc)
        logger.warning(
            COORDINATION_CLEANUP_FAILED,
            parent_task_id=task_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            context="post_completion_coordination_metrics",
        )


def build_collection_inputs(
    task_id: str,
    dispatch_result: DispatchResult,
) -> CollectionInputs | None:
    """Aggregate sub-agent results into the collector inputs.

    The aggregate ``ExecutionResult`` carries the team-wide turn records
    (``model_copy`` off a real sub-agent result, swapping only ``turns``
    since the collector reads nothing else off it) so ``turns_mas`` is the
    total reasoning turns across the system.

    Returns:
        The :class:`CollectionInputs` payload ready for the collector, or
        ``None`` when no sub-agent produced a result.
    """
    outcomes = [
        outcome
        for wave in dispatch_result.waves
        if wave.execution_result is not None
        for outcome in wave.execution_result.outcomes
    ]
    results = [outcome.result for outcome in outcomes if outcome.result is not None]
    if not results:
        return None
    # Count every dispatched participant, including ones whose subtask failed
    # (no result), so team-level metrics are not skewed low by partial failures.
    participating_agents = {outcome.agent_id for outcome in outcomes}
    aggregate_turns = tuple(turn for r in results for turn in r.execution_result.turns)
    aggregate = results[0].execution_result.model_copy(
        update={"turns": aggregate_turns},
    )
    # Sum durations per agent so StragglerGap reflects each actor's total time
    # across waves rather than a single subtask slice.
    durations_by_agent: dict[str, float] = {}
    for r in results:
        durations_by_agent[r.agent_id] = (
            durations_by_agent.get(r.agent_id, 0.0) + r.duration_seconds
        )
    return CollectionInputs(
        execution_result=aggregate,
        agent_id=COORDINATOR_ACTOR,
        task_id=task_id,
        team_size=len(participating_agents),
        agent_durations=tuple(durations_by_agent.items()),
        agent_outputs=tuple(
            r.completion_summary for r in results if r.completion_summary
        ),
        is_multi_agent=True,
    )


__all__ = [
    "COORDINATOR_ACTOR",
    "build_collection_inputs",
    "collect_coordination_metrics",
]
