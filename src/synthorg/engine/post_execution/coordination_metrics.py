# module-kind: code
"""Recording what one finished run cost the coordination picture.

A best-effort side channel, like the memory hooks beside it: the run has
already landed and its task has already moved, so a collector fault must
not reach the caller. It is logged rather than swallowed, because a
collector that has stopped answering makes every coordination figure quietly
older than it looks.
"""

from synthorg.budget.coordination_collector import (
    CollectionInputs,
    CoordinationMetricsCollector,
    collect_bounded,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.loop_protocol import ExecutionResult
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import EXECUTION_ENGINE_ERROR

logger = get_logger(__name__)


async def try_collect_coordination_metrics(
    execution_result: ExecutionResult,
    agent_id: str,
    task_id: str,
    *,
    collector: CoordinationMetricsCollector | None,
) -> None:
    """Collect coordination metrics for a finished run, never fatally.

    Args:
        execution_result: The finished run.
        agent_id: The agent that ran it.
        task_id: The task it ran.
        collector: The wired collector, or ``None``.
    """
    if collector is None:
        return
    try:
        await collect_bounded(
            collector,
            CollectionInputs(
                execution_result=execution_result,
                agent_id=agent_id,
                task_id=task_id,
                is_multi_agent=False,
            ),
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort side channel
        reraise_critical(exc)
        logger.warning(
            EXECUTION_ENGINE_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            reason="coordination_metrics_failed",
        )


__all__ = ["try_collect_coordination_metrics"]
