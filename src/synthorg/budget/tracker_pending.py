# module-kind: code
"""Strong references to the tracker's in-flight background record tasks.

Its own module because it is asyncio task lifetime, not cost arithmetic:
what it owns is a set of tasks and the rules for draining them, and it
changes for scheduling reasons rather than billing ones.
"""

import asyncio

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.budget import (
    BUDGET_PENDING_RECORD_DRAIN_UNEXPECTED,
)

logger = get_logger(__name__)


class PendingRecordTasks:
    """Registry of background ``CostTracker.record`` tasks.

    Owned per tracker (rather than module-wide) so each test, which builds
    its own tracker, gets a fresh set: a task leaked from a prior test is
    bound to a closed event loop and would otherwise poison the next one.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def __len__(self) -> int:
        """Number of tasks still in flight.

        Returns:
            The count of tracked, not-yet-evicted tasks.
        """
        return len(self._tasks)

    def track(self, task: asyncio.Task[None]) -> None:
        """Hold a strong reference to a background recording task.

        The cost-recording chokepoint schedules ``cost_tracker.record(...)``
        as a background task so the user-visible ``provider.complete()``
        response is never blocked on tracker I/O. asyncio's loop only keeps
        weak references to tasks, so without an external strong reference
        the loop's GC may cancel an in-flight task. A self-eviction callback
        keeps the set no larger than what is actually in flight.

        Args:
            task: The scheduled recording task.
        """
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Wait for all in-flight background record tasks to settle.

        Test-only utility: tests that need to observe ``CostTracker`` state
        immediately after a ``provider.complete()`` call can await this to
        deterministically wait for the recording side effect.

        No-op when there are no pending tasks. Recoverable failures inside
        the background tasks are already logged and swallowed in
        ``_record_cost_in_background`` (see
        :mod:`synthorg.providers.cost_recording`).
        :class:`MemoryError` and :class:`RecursionError` propagate so a
        drain invoked from a test path does not silently swallow
        interpreter-fatal signals via ``return_exceptions=True``.
        :class:`asyncio.CancelledError` is re-raised so cancellation
        propagates instead of producing a misleading WARN log: a cancelled
        background task is the *expected* outcome of a graceful shutdown or
        a test cancelling the surrounding ``TaskGroup``, not a regression.

        Raises:
            MemoryError: Propagated from a background task, never swallowed.
            RecursionError: Propagated from a background task, never swallowed.
            CancelledError: Re-raised so cancellation propagates.
        """
        if not self._tasks:
            return
        # Re-drain until the set is empty: a new record task can be added
        # (via ``track``) WHILE we await ``gather`` below, so a single
        # snapshot would miss it. ``difference_update`` removes the
        # just-drained tasks immediately (rather than waiting on the
        # add_done_callback to fire), so the loop converges once no new
        # task arrives.
        results: list[BaseException | None] = []
        while self._tasks:
            pending = tuple(self._tasks)
            results.extend(await asyncio.gather(*pending, return_exceptions=True))
            self._tasks.difference_update(pending)
        cancelled_count = 0
        for outcome in results:
            if isinstance(outcome, (MemoryError, RecursionError)):
                raise outcome
            if isinstance(outcome, asyncio.CancelledError):
                # Cancellation is expected during graceful shutdown; count
                # for the propagation below but do not WARN.
                cancelled_count += 1
                continue
            if isinstance(outcome, BaseException):
                # ``_record_cost_in_background`` already logs and swallows
                # recoverable failures, so reaching this branch means
                # something downstream raised without going through the
                # documented logging path. Surface defensively at WARN so
                # the regression is visible in test output rather than
                # silently dropped by ``return_exceptions=True``.
                logger.warning(
                    BUDGET_PENDING_RECORD_DRAIN_UNEXPECTED,
                    error_type=type(outcome).__name__,
                    error=safe_error_description(outcome),
                )
        if cancelled_count:
            # Re-raise a CancelledError so the caller's surrounding
            # TaskGroup / context observes the cancellation instead of
            # silently masking it. The specific instance is not preserved
            # because the gather snapshot may hold many; one suffices to
            # propagate the signal.
            raise asyncio.CancelledError


__all__ = ["PendingRecordTasks"]
