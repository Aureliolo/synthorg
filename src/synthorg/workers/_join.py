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

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workers import WORKERS_SUBTASK_JOIN_TIMEOUT

logger = get_logger(__name__)

_SUBTASK_JOIN_TIMEOUT_SECONDS: Final[float] = 5.0
"""Ceiling on joining a just-cancelled sub-task before it is abandoned.
Comfortably above a routine cancellation (which lands on the sub-task's
next ``await``) yet well below any caller shutdown deadline, so a wedged
broker degrades to a logged orphan rather than a hung teardown."""

_ABANDONED_TASKS: Final[set[asyncio.Task[None]]] = set()
"""Strong references to timed-out joins. asyncio keeps only a weak reference
to a task, so a still-pending orphan with no owner -- once ``run()`` /
``_execute_claim()`` returns -- could be garbage-collected before its own
await unwinds. Each task removes itself via a done-callback once it settles."""


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

    Raises:
        BaseException: Whatever the sub-task itself raised when it settled
            without being cancelled -- e.g. a critical re-raised through
            ``reraise_critical`` inside the heartbeat / ack-extender loop.
            Such a failure must surface, not be dropped as an unretrieved-task
            warning.
    """
    _done, pending = await asyncio.wait({task}, timeout=timeout_seconds)
    if pending:
        # Wedged past the deadline: abandon it, but retain a strong reference
        # so the orphan is not garbage-collected before its await unwinds. The
        # reap callback both drops that reference and consumes any exception the
        # orphan later raises, so a late failure does not surface as a noisy,
        # context-free unretrieved-exception warning from asyncio's handler.
        def _reap(finished: asyncio.Task[None]) -> None:
            _ABANDONED_TASKS.discard(finished)
            if finished.cancelled():
                return
            late_exc = finished.exception()
            if late_exc is not None:
                logger.warning(
                    WORKERS_SUBTASK_JOIN_TIMEOUT,
                    worker_id=worker_id,
                    subtask=label,
                    error_type=type(late_exc).__name__,
                    error=safe_error_description(late_exc),
                )

        _ABANDONED_TASKS.add(task)
        task.add_done_callback(_reap)
        logger.warning(
            WORKERS_SUBTASK_JOIN_TIMEOUT,
            worker_id=worker_id,
            subtask=label,
        )
        return
    # Settled within the deadline. A clean cancellation is expected; any other
    # completion carrying an exception must propagate rather than be swallowed.
    if not task.cancelled() and (exc := task.exception()) is not None:
        raise exc


__all__ = ["join_cancelled"]
