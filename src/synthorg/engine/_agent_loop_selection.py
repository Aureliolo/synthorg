# module-kind: code
"""Choosing which execution loop runs a task.

Selection is a decision over the task's complexity. It is a pure read of
configuration, so it lives outside the engine's factory mixin: the mixin
supplies the wiring (the static loop, a builder bound to the engine's
dependencies) and this decides.
"""

from collections.abc import Callable

from synthorg.core.task import Task
from synthorg.engine.loop_protocol import ExecutionLoop
from synthorg.engine.loop_selector import AutoLoopConfig, select_loop_type
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_AUTO_SELECTED,
    EXECUTION_LOOP_STATIC_SELECTED,
)

logger = get_logger(__name__)

#: Builds a loop of the named type from the engine's own dependencies.
type LoopBuilder = Callable[[str], ExecutionLoop]


async def resolve_loop(
    task: Task,
    *,
    agent_id: str,
    task_id: str,
    static_loop: ExecutionLoop,
    auto_loop_config: AutoLoopConfig | None,
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
        build: Builds the selected loop from the engine's dependencies.

    Returns:
        The configured default loop when auto-selection is off; otherwise
        a loop of the type selected from task complexity.
    """
    if auto_loop_config is None:
        logger.debug(
            EXECUTION_LOOP_STATIC_SELECTED,
            agent_id=agent_id,
            task_id=task_id,
            loop_type=static_loop.get_loop_type(),
        )
        return static_loop

    loop_type = select_loop_type(
        complexity=task.estimated_complexity,
        rules=auto_loop_config.rules,
        default_loop_type=auto_loop_config.default_loop_type,
    )

    logger.info(
        EXECUTION_LOOP_AUTO_SELECTED,
        agent_id=agent_id,
        task_id=task_id,
        complexity=task.estimated_complexity.value,
        selected_loop=loop_type,
    )
    return build(loop_type)


__all__ = ["LoopBuilder", "resolve_loop"]
