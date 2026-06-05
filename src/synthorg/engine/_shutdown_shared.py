"""Shared constants and helpers for the shutdown strategies.

``_DEFAULT_CLEANUP_SECONDS`` is the post-wait cancellation window used by
all three strategies. ``_DEFAULT_GRACE_SECONDS`` is the cooperative grace
window used only by ``CheckpointAndStopStrategy``. ``_count_cooperative_exits``
tallies cooperative versus errored exits for ``FinishCurrentToolStrategy``
and ``CheckpointAndStopStrategy``.
"""

import asyncio
from typing import Any, Final

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_SHUTDOWN_TASK_ERROR,
)

logger = get_logger(__name__)

_DEFAULT_CLEANUP_SECONDS: Final[float] = 5.0
_DEFAULT_GRACE_SECONDS: Final[float] = 30.0


def _count_cooperative_exits(
    done: set[asyncio.Task[Any]],
) -> tuple[int, int]:
    """Count tasks that exited cooperatively and those that errored.

    Tasks that raised exceptions are logged at WARNING.

    Args:
        done: Set of completed asyncio tasks.

    Returns:
        Tuple of (completed_count, errored_count).
    """
    completed = 0
    errored = 0
    for task in done:
        if task.cancelled():
            continue
        exc = task.exception()
        if exc is not None:
            errored += 1
            logger.warning(
                EXECUTION_SHUTDOWN_TASK_ERROR,
                context="task_raised_during_shutdown",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        else:
            completed += 1
    return completed, errored
