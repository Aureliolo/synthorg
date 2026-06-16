"""Immediate-cancel shutdown strategy.

Force-cancels all agent tasks immediately with no grace period.
Satisfies the ``ShutdownStrategy`` protocol from
``synthorg.engine.shutdown``.
"""

import asyncio
from collections.abc import Mapping, Sequence

from synthorg.core.clock import Clock, SystemClock
from synthorg.engine._shutdown_shared import _DEFAULT_CLEANUP_SECONDS
from synthorg.engine.shutdown import (
    CleanupCallback,
    ShutdownResult,
    _log_post_cancel_exceptions,
    _run_cleanup,
)
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_SHUTDOWN_COMPLETE,
    EXECUTION_SHUTDOWN_IMMEDIATE_CANCEL,
    EXECUTION_SHUTDOWN_TASK_ERROR,
)

logger = get_logger(__name__)


class ImmediateCancelStrategy:
    """Immediate cancel shutdown strategy.

    Force-cancel all agent tasks immediately with no grace period.
    Fastest shutdown but highest data loss -- partial tool side effects,
    billed-but-lost LLM responses.
    """

    def __init__(
        self,
        *,
        cleanup_seconds: float = _DEFAULT_CLEANUP_SECONDS,
        clock: Clock | None = None,
    ) -> None:
        """Initialize the strategy.

        Args:
            cleanup_seconds: Grace window for the post-cancel cleanup
                callbacks before they themselves are timed out.  Must
                be positive.
            clock: Injectable time source; defaults to ``SystemClock``.

        Raises:
            ValueError: If ``cleanup_seconds`` is non-positive.
        """
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
        self._cleanup_seconds = cleanup_seconds
        self._shutdown_event = asyncio.Event()

    def request_shutdown(self) -> None:
        """Signal that a graceful shutdown has been requested."""
        self._shutdown_event.set()

    def clear_shutdown(self) -> None:
        """Reopen the drain gate for a reused strategy (lifespan re-entry)."""
        self._shutdown_event.clear()

    def is_shutting_down(self) -> bool:
        """Return ``True`` when shutdown has been requested."""
        return self._shutdown_event.is_set()

    def get_strategy_type(self) -> str:
        """Return the strategy identifier."""
        return "immediate"

    # Internal constant by design: bounds cancellation propagation so
    # the total shutdown stays within the lifecycle budget; not
    # exposed to the settings registry.
    _CANCEL_PROPAGATION_TIMEOUT: float = 5.0

    async def execute_shutdown(
        self,
        *,
        running_tasks: Mapping[str, asyncio.Task[object]],
        cleanup_callbacks: Sequence[CleanupCallback],
    ) -> ShutdownResult:
        """Cancel all tasks immediately, then run cleanup.

        Returns:
            A :class:`ShutdownResult` reporting every task as
            ``tasks_interrupted`` (immediate cancel never counts
            cooperative completion).
        """
        start = self._clock.monotonic()
        self._shutdown_event.set()

        task_set = set(running_tasks.values())
        tasks_interrupted = len(task_set)

        if task_set:
            logger.info(
                EXECUTION_SHUTDOWN_IMMEDIATE_CANCEL,
                running_tasks=tasks_interrupted,
            )
            for task in task_set:
                task.cancel()
            cancel_done, _ = await asyncio.wait(
                task_set,
                timeout=self._CANCEL_PROPAGATION_TIMEOUT,
            )
            _log_post_cancel_exceptions(cancel_done)

        cleanup_completed = await _run_cleanup(
            cleanup_callbacks,
            self._cleanup_seconds,
        )

        result = ShutdownResult(
            strategy_type=self.get_strategy_type(),
            tasks_interrupted=tasks_interrupted,
            tasks_completed=0,
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
