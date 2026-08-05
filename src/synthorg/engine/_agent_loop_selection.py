# module-kind: code
"""Choosing which execution loop runs a task.

Selection is a decision over the task's complexity and, for the hybrid
candidate only, the live budget utilisation. It is a pure read of
configuration, so it lives outside the engine's factory mixin: the mixin
supplies the wiring (the static loop, the budget read, a builder bound to
the engine's dependencies) and this decides.

The budget read is deliberately conditional. It is a live query with a real
cost, and only the hybrid candidate can be downgraded by it, so a task that
selects any other loop never pays for an answer that could not change the
outcome.
"""

from collections.abc import Awaitable, Callable
from typing import Final

from synthorg.core.task import Task
from synthorg.engine.loop_protocol import ExecutionLoop
from synthorg.engine.loop_selector import AutoLoopConfig, select_loop_type
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_AUTO_SELECTED,
    EXECUTION_LOOP_BUDGET_UNAVAILABLE,
    EXECUTION_LOOP_STATIC_SELECTED,
)

logger = get_logger(__name__)

#: Reads the live budget utilisation, or ``None`` when it is unknowable.
type BudgetUtilizationReader = Callable[[], Awaitable[float | None]]

#: Builds a loop of the named type from the engine's own dependencies.
type LoopBuilder = Callable[[str], ExecutionLoop]

#: The loop candidate whose selection the budget can override.
_BUDGET_SENSITIVE_LOOP: Final[str] = "hybrid"


async def resolve_loop(
    task: Task,
    *,
    agent_id: str,
    task_id: str,
    static_loop: ExecutionLoop,
    auto_loop_config: AutoLoopConfig | None,
    budget_utilization: BudgetUtilizationReader | None,
    build: LoopBuilder,
) -> ExecutionLoop:
    """Select the execution loop for a task.

    Args:
        task: The task about to run; its complexity drives the rules.
        agent_id: Agent the loop is being selected for, for the log line.
        task_id: Task the loop is being selected for, for the log line.
        static_loop: The configured loop, returned as-is when
            auto-selection is off.
        auto_loop_config: Auto-selection rules, or ``None`` when the
            operator pinned one loop.
        budget_utilization: Live budget-utilisation read, awaited only when
            the preliminary pick is the budget-sensitive candidate.
        build: Builds the selected loop from the engine's dependencies.

    Returns:
        The configured default loop when auto-selection is off; otherwise
        a loop of the type selected from task complexity and (when
        relevant) live budget utilisation.
    """
    if auto_loop_config is None:
        logger.debug(
            EXECUTION_LOOP_STATIC_SELECTED,
            agent_id=agent_id,
            task_id=task_id,
            loop_type=static_loop.get_loop_type(),
        )
        return static_loop

    preliminary = select_loop_type(
        complexity=task.estimated_complexity,
        rules=auto_loop_config.rules,
        budget_utilization_pct=None,
        budget_tight_threshold=auto_loop_config.budget_tight_threshold,
        hybrid_fallback=None,
        default_loop_type=auto_loop_config.default_loop_type,
    )

    utilization_pct: float | None = None
    if preliminary == _BUDGET_SENSITIVE_LOOP and budget_utilization is not None:
        utilization_pct = await budget_utilization()
        if utilization_pct is None:
            logger.debug(
                EXECUTION_LOOP_BUDGET_UNAVAILABLE,
                note="budget utilization unknown; skipping budget-aware downgrade",
            )

    loop_type = select_loop_type(
        complexity=task.estimated_complexity,
        rules=auto_loop_config.rules,
        budget_utilization_pct=utilization_pct,
        budget_tight_threshold=auto_loop_config.budget_tight_threshold,
        hybrid_fallback=auto_loop_config.hybrid_fallback,
        default_loop_type=auto_loop_config.default_loop_type,
    )

    logger.info(
        EXECUTION_LOOP_AUTO_SELECTED,
        agent_id=agent_id,
        task_id=task_id,
        complexity=task.estimated_complexity.value,
        selected_loop=loop_type,
        budget_utilization_pct=utilization_pct,
    )
    return build(loop_type)


__all__ = ["BudgetUtilizationReader", "LoopBuilder", "resolve_loop"]
