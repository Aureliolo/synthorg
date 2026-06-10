"""Background task tracking for fire-and-forget coroutines.

Provides :class:`BackgroundTaskRegistry` -- a minimal utility that
spawns asyncio tasks, tracks them in a set, discards them on
completion, and logs failures via a done-callback. Use wherever a
subsystem must emit a best-effort notification in an exception path
and cannot ``await`` it because the primary exception must propagate
immediately (e.g. budget-exhaustion notifications that precede
``raise BudgetExhaustedError``).

Without this registry, tasks created via :func:`asyncio.create_task`
that raise silently vanish -- the coroutine's exception is only
surfaced as a garbage-collector warning, which CI and operators
never see.
"""

import asyncio
import copy
import time
from collections.abc import Callable, Coroutine, Mapping
from types import MappingProxyType
from typing import Final, Protocol

from synthorg.observability import get_logger
from synthorg.observability.events.async_task import (
    BACKGROUND_TASKS_DRAIN_TIMEOUT,
)
from synthorg.observability.events.notification import NOTIFICATION_SEND_FAILED

logger = get_logger(__name__)

_DEFAULT_DRAIN_TIMEOUT_SECONDS: Final[float] = 5.0


class BackgroundTaskRegistry:
    """Tracks fire-and-forget asyncio tasks so failures surface in logs.

    Example:
        Inside a subsystem that must fire a best-effort notification
        on an exception path and then re-raise::

            self._tasks.spawn(
                self._notify(title, body),
                event=NOTIFICATION_BUDGET_EXHAUSTED_SEND,
                severity="critical",
            )
            raise BudgetExhaustedError(msg)

        On exception inside ``self._notify``, the done-callback logs
        :const:`synthorg.observability.events.notification.NOTIFICATION_SEND_FAILED`
        at ERROR with ``exc_info`` and the context passed to ``spawn``.

    Args:
        owner: Short identifier for the subsystem that owns this
            registry, used as a log field (e.g. ``"budget.enforcer"``).
    """

    def __init__(self, *, owner: str) -> None:
        self._owner = owner
        self._tasks: set[asyncio.Task[object]] = set()

    def spawn(
        self,
        coro: Coroutine[object, object, object],
        *,
        event: str,
        **context: object,
    ) -> asyncio.Task[object]:
        """Create and track a background task.

        Args:
            coro: The coroutine to run.
            event: Intent event constant describing what this task is
                *trying* to do (e.g.
                ``NOTIFICATION_BUDGET_EXHAUSTED_SEND``). Included in
                the failure log as ``intent_event`` so operators can
                identify which notification failed without reading
                the stack trace.
            **context: Structured kwargs merged into the failure log
                (e.g. ``severity="critical"``, ``agent_id=...``).

        Returns:
            The created :class:`asyncio.Task`.
        """
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        # Deep-freeze the context up-front so neither the caller's
        # ``**kwargs`` source nor any nested mutable value can race
        # the done-callback's log line. ``dict(context)`` alone was
        # only shallow -- this matches the repo's convention of
        # ``copy.deepcopy`` + ``MappingProxyType`` for internal
        # non-Pydantic collections.
        frozen_context = MappingProxyType(copy.deepcopy(context))
        task.add_done_callback(
            self._make_done_callback(event, frozen_context),
        )
        return task

    def _make_done_callback(
        self,
        event: str,
        context: Mapping[str, object],
    ) -> Callable[[asyncio.Task[object]], None]:
        """Build a done-callback that discards the task and logs failures.

        Returns:
            A callback that removes the finished task from the tracking
            set and logs a redacted failure record if it raised.
        """
        owner = self._owner
        tasks = self._tasks

        def _on_done(task: asyncio.Task[object]) -> None:
            tasks.discard(task)
            if task.cancelled():
                return
            exc = task.exception()
            if exc is None:
                return
            # Resource-exhaustion errors are logged at CRITICAL and
            # routed to the event loop's exception handler rather
            # than re-raised: done-callbacks run inside the loop,
            # and a raise here is swallowed by asyncio's callback
            # machinery -- it does not propagate to the caller.
            if isinstance(exc, MemoryError | RecursionError):
                logger.critical(
                    NOTIFICATION_SEND_FAILED,
                    owner=owner,
                    intent_event=event,
                    error_type=type(exc).__name__,
                    exc_info=(type(exc), exc, exc.__traceback__),
                    **context,
                )
                # Done-callbacks usually run while the loop is alive, but
                # a registry shared across lifespan boundaries may fire a
                # last callback after the loop has closed. A missing loop
                # must not mask the fatal log we just emitted.
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    return
                loop.call_exception_handler(
                    {
                        "message": "fatal exception in tracked background task",
                        "exception": exc,
                        "task": task,
                    }
                )
                return
            logger.error(
                NOTIFICATION_SEND_FAILED,
                owner=owner,
                intent_event=event,
                error_type=type(exc).__name__,
                exc_info=(type(exc), exc, exc.__traceback__),
                **context,
            )

        return _on_done

    async def drain(
        self, *, timeout_sec: float = _DEFAULT_DRAIN_TIMEOUT_SECONDS
    ) -> None:
        """Wait for all tracked tasks to complete.

        On timeout, logs a warning at
        :const:`BACKGROUND_TASKS_DRAIN_TIMEOUT` and cancels pending
        tasks so shutdown is bounded. Uses :func:`asyncio.wait`
        (which does **not** cancel tasks on timeout) so the log's
        ``pending_count`` accurately reflects tasks that exceeded the
        deadline, rather than being racily zeroed by
        :func:`asyncio.gather`-style cancellation propagation.

        Args:
            timeout_sec: Maximum wait time in seconds.
        """
        if not self._tasks:
            return
        pending = tuple(self._tasks)
        start = time.monotonic()
        _, still_pending = await asyncio.wait(pending, timeout=timeout_sec)
        if not still_pending:
            return
        logger.warning(
            BACKGROUND_TASKS_DRAIN_TIMEOUT,
            owner=self._owner,
            pending_count=len(still_pending),
            timeout_sec=timeout_sec,
        )
        for task in still_pending:
            task.cancel()
        # Give cancelled tasks the remainder of the deadline to run
        # their done-callbacks so ``active_count`` drops to zero;
        # the caller asked for a ``timeout_sec`` bound, not
        # ``2 * timeout_sec``. A task that catches ``CancelledError``
        # without re-raising still exits at most at the original
        # deadline.
        elapsed = time.monotonic() - start
        remaining = max(0.0, timeout_sec - elapsed)
        await asyncio.wait(still_pending, timeout=remaining)

    @property
    def active_count(self) -> int:
        """Return the number of tasks still pending."""
        return len(self._tasks)


class _TaskExceptionLogger(Protocol):
    """Narrow structlog-proxy surface used by :func:`log_task_exceptions`.

    Declares only the two level methods the done-callback invokes, so
    the helper avoids an explicit-``Any`` escape hatch while still
    accepting the lazy structlog proxy returned by
    :func:`synthorg.observability.get_logger`.
    """

    def critical(self, event: str, /, **context: object) -> None: ...

    def warning(self, event: str, /, **context: object) -> None: ...


def log_task_exceptions(
    logger_: _TaskExceptionLogger,
    event: str,
    **context: object,
) -> Callable[[asyncio.Task[object]], None]:
    """Build an :meth:`asyncio.Task.add_done_callback`-compatible callback.

    This is a plain factory -- it returns a single callback and does
    no task tracking.  Unlike :class:`BackgroundTaskRegistry` (which
    owns a set of fire-and-forget tasks and drains them on shutdown),
    this helper is for callers who manage the task's lifecycle
    themselves and only need exception-to-log routing.  Typical
    targets are long-lived *named* tasks whose lifecycle matches the
    owning subsystem (task-engine processing loop, bus-bridge poll
    loop, meeting scheduler tick), but the callback is safe for any
    :class:`asyncio.Task`.  The returned callback:

    * Ignores ``CancelledError`` (normal shutdown).
    * Escalates ``MemoryError``/``RecursionError`` to CRITICAL + the
      event-loop exception handler (re-raising from a done-callback
      would be swallowed by asyncio).
    * Logs everything else at WARNING with ``exc_info`` + the task's
      name so operators can identify which long-lived worker died.

    Args:
        logger_: Structlog logger for the owning subsystem.
        event: Event constant to log under (e.g.
            ``TASK_ENGINE_LOOP_DIED``).  Caller-owned so per-subsystem
            taxonomy stays consistent with existing sinks.
        **context: Structured kwargs merged into the failure log
            (e.g. ``channel=...`` for bus bridge channels).

    Returns:
        Callable usable as ``task.add_done_callback(...)``.
    """
    frozen_context = MappingProxyType(copy.deepcopy(context))

    def _on_done(task: asyncio.Task[object]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        if isinstance(exc, MemoryError | RecursionError):
            logger_.critical(
                event,
                task_name=task.get_name(),
                error_type=type(exc).__name__,
                exc_info=(type(exc), exc, exc.__traceback__),
                **frozen_context,
            )
            # A done-callback can fire after the owning loop has
            # closed (e.g. the task was cancelled during shutdown but
            # its callback queued post-loop-stop). Missing the
            # handler registration must not mask the fatal log above.
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            loop.call_exception_handler(
                {
                    "message": "fatal exception in long-lived background task",
                    "exception": exc,
                    "task": task,
                }
            )
            return
        logger_.warning(
            event,
            task_name=task.get_name(),
            error_type=type(exc).__name__,
            exc_info=(type(exc), exc, exc.__traceback__),
            **frozen_context,
        )

    return _on_done
