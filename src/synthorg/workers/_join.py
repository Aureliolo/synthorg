# module-kind: code
"""Bounded cancel-join for a worker's background sub-tasks.

Cancelling a sub-task and then ``await``-ing it inside
``contextlib.suppress(asyncio.CancelledError)`` is unsafe on a teardown
path: the suppress swallows a ``CancelledError`` from ANY source, so a
shutdown ``asyncio.timeout`` firing while the sub-task is wedged on an
unreachable broker gets eaten and teardown hangs to the process / suite
timeout (which SIGABRTs the test worker). ``asyncio.wait`` bounds the join
and never re-raises the joined task's own cancellation, while still letting
an EXTERNAL cancel of the caller propagate -- so the caller's shutdown
deadline stays effective.
"""

import asyncio
from typing import Final

from synthorg.observability import get_logger
from synthorg.observability.events.workers import WORKERS_SUBTASK_JOIN_TIMEOUT

logger = get_logger(__name__)

_SUBTASK_JOIN_TIMEOUT_SECONDS: Final[float] = 5.0
"""Ceiling on joining a just-cancelled sub-task before it is abandoned.
Comfortably above a routine cancellation (which lands on the sub-task's
next ``await``) yet well below any caller shutdown deadline, so a wedged
broker degrades to a logged orphan rather than a hung teardown."""


async def join_cancelled(
    task: asyncio.Task[None],
    worker_id: str,
    label: str,
    *,
    timeout_seconds: float = _SUBTASK_JOIN_TIMEOUT_SECONDS,
) -> None:
    """Bounded-join *task* (already cancelled by the caller); abandon on timeout.

    Args:
        task: The sub-task the caller has just ``cancel()``-ed.
        worker_id: Owning worker id, carried on the abandonment warning.
        label: Human name of the sub-task (e.g. ``"heartbeat"``).
        timeout_seconds: Seconds to wait for the task to finish cancelling
            before abandoning it as a logged orphan.
    """
    _done, pending = await asyncio.wait({task}, timeout=timeout_seconds)
    if pending:
        logger.warning(
            WORKERS_SUBTASK_JOIN_TIMEOUT,
            worker_id=worker_id,
            subtask=label,
        )


__all__ = ["join_cancelled"]
