"""Finish-current-tool shutdown strategy.

Allows the current tool invocation a per-tool timeout to complete, then
force-cancels stragglers. Satisfies the ``ShutdownStrategy`` protocol
from ``synthorg.engine.shutdown``.
"""

import asyncio
from typing import TYPE_CHECKING, Any

from synthorg.core.clock import Clock, SystemClock
from synthorg.engine._shutdown_shared import (
    _DEFAULT_CLEANUP_SECONDS,
    _count_cooperative_exits,
)
from synthorg.engine.shutdown import (
    CleanupCallback,
    ShutdownResult,
    _log_post_cancel_exceptions,
    _run_cleanup,
)
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_SHUTDOWN_COMPLETE,
    EXECUTION_SHUTDOWN_FORCE_CANCEL,
    EXECUTION_SHUTDOWN_TASK_ERROR,
    EXECUTION_SHUTDOWN_TOOL_WAIT,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

logger = get_logger(__name__)


class FinishCurrentToolStrategy:
    """Finish current tool shutdown strategy.

    Like cooperative timeout, but uses a per-tool timeout (default 60s)
    to allow the current tool invocation to complete.  The execution
    loop already finishes the current tool before checking shutdown at
    turn boundaries; this strategy gives a longer window for that.
    """

    def __init__(
        self,
        *,
        tool_timeout_seconds: float,
        cleanup_seconds: float = _DEFAULT_CLEANUP_SECONDS,
        clock: Clock | None = None,
    ) -> None:
        """Initialize the strategy.

        Args:
            tool_timeout_seconds: Per-tool grace window before the
                running invocation is force-cancelled.  Must be
                positive.  Operator-tunable; resolve via
                ``ConfigResolver.get_float("engine",
                "shutdown_tool_timeout_seconds")`` at the call site.
            cleanup_seconds: Grace window for the post-cancel cleanup
                callbacks before they themselves are timed out.  Must
                be positive.
            clock: Injectable time source; defaults to ``SystemClock``.

        Raises:
            ValueError: If either parameter is non-positive.
        """
        if tool_timeout_seconds <= 0:
            msg = f"tool_timeout_seconds must be positive, got {tool_timeout_seconds}"
            logger.warning(
                EXECUTION_SHUTDOWN_TASK_ERROR,
                error=msg,
                param="tool_timeout_seconds",
                value=tool_timeout_seconds,
            )
            raise ValueError(msg)
        if cleanup_seconds <= 0:
            msg = f"cleanup_seconds must be positive, got {cleanup_seconds}"
            logger.warning(
                EXECUTION_SHUTDOWN_TASK_ERROR,
                error=msg,
                param="cleanup_seconds",
                value=cleanup_seconds,
            )
            raise ValueError(msg)
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._tool_timeout_seconds = tool_timeout_seconds
        self._cleanup_seconds = cleanup_seconds
        self._shutdown_event = asyncio.Event()

    def request_shutdown(self) -> None:
        """Signal that a graceful shutdown has been requested."""
        self._shutdown_event.set()

    def is_shutting_down(self) -> bool:
        """Return ``True`` when shutdown has been requested."""
        return self._shutdown_event.is_set()

    def get_strategy_type(self) -> str:
        """Return the strategy identifier."""
        return "finish_tool"

    # Internal constant by design: bounds cancellation propagation so
    # the total shutdown stays within the lifecycle budget; not
    # exposed to the settings registry.
    _CANCEL_PROPAGATION_TIMEOUT: float = 5.0

    async def execute_shutdown(
        self,
        *,
        running_tasks: Mapping[str, asyncio.Task[Any]],
        cleanup_callbacks: Sequence[CleanupCallback],
    ) -> ShutdownResult:
        """Wait for current tool, then cancel stragglers.

        Returns:
            A :class:`ShutdownResult` reporting cooperatively-finished
            tasks under ``tasks_completed`` and the rest (force-
            cancelled + errored) under ``tasks_interrupted``.
        """
        start = self._clock.monotonic()
        self._shutdown_event.set()

        logger.info(
            EXECUTION_SHUTDOWN_TOOL_WAIT,
            tool_timeout_seconds=self._tool_timeout_seconds,
            running_tasks=len(running_tasks),
        )

        if not running_tasks:
            cleanup_completed = await _run_cleanup(
                cleanup_callbacks,
                self._cleanup_seconds,
            )
            result = ShutdownResult(
                strategy_type=self.get_strategy_type(),
                tasks_interrupted=0,
                tasks_completed=0,
                cleanup_completed=cleanup_completed,
                duration_seconds=self._clock.monotonic() - start,
            )
            logger.info(
                EXECUTION_SHUTDOWN_COMPLETE,
                strategy=result.strategy_type,
                tasks_interrupted=0,
                tasks_completed=0,
                cleanup_completed=result.cleanup_completed,
                duration_seconds=result.duration_seconds,
            )
            return result

        task_set = set(running_tasks.values())
        done, pending = await asyncio.wait(
            task_set,
            timeout=self._tool_timeout_seconds,
        )

        tasks_completed, tasks_errored = _count_cooperative_exits(done)

        # Force-cancel stragglers.
        if pending:
            logger.warning(
                EXECUTION_SHUTDOWN_FORCE_CANCEL,
                pending_tasks=len(pending),
            )
            for task in pending:
                task.cancel()
            cancel_done, _ = await asyncio.wait(
                pending,
                timeout=self._CANCEL_PROPAGATION_TIMEOUT,
            )
            _log_post_cancel_exceptions(cancel_done)

        cleanup_completed = await _run_cleanup(
            cleanup_callbacks,
            self._cleanup_seconds,
        )

        result = ShutdownResult(
            strategy_type=self.get_strategy_type(),
            tasks_interrupted=len(pending) + tasks_errored,
            tasks_completed=tasks_completed,
            cleanup_completed=cleanup_completed,
            duration_seconds=self._clock.monotonic() - start,
        )
        logger.info(
            EXECUTION_SHUTDOWN_COMPLETE,
            strategy=result.strategy_type,
            tasks_interrupted=result.tasks_interrupted,
            tasks_completed=result.tasks_completed,
            cleanup_completed=result.cleanup_completed,
            duration_seconds=result.duration_seconds,
        )
        return result
