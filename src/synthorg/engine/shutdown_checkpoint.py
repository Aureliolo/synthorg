"""Checkpoint-and-stop shutdown strategy.

Agents checkpoint cooperatively during a grace period; stragglers are
checkpointed via the ``checkpoint_saver`` callback (if provided) then
cancelled. Satisfies the ``ShutdownStrategy`` protocol from
``synthorg.engine.shutdown``.
"""

import asyncio
from collections.abc import Mapping, Sequence
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine._shutdown_shared import (
    _DEFAULT_CLEANUP_SECONDS,
    _DEFAULT_GRACE_SECONDS,
    _count_cooperative_exits,
)
from synthorg.engine.shutdown import (
    CheckpointSaver,
    CleanupCallback,
    ShutdownResult,
    _log_post_cancel_exceptions,
    _run_cleanup,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_SHUTDOWN_CHECKPOINT_FAILED,
    EXECUTION_SHUTDOWN_CHECKPOINT_SAVE,
    EXECUTION_SHUTDOWN_COMPLETE,
    EXECUTION_SHUTDOWN_GRACE_START,
    EXECUTION_SHUTDOWN_TASK_ERROR,
)

logger = get_logger(__name__)


class CheckpointAndStopStrategy:
    """Checkpoint and stop shutdown strategy.

    On shutdown signal, agents checkpoint cooperatively during the
    grace period.  Stragglers are checkpointed via the
    ``checkpoint_saver`` callback (if provided), then cancelled.
    Tasks that are successfully checkpointed are reported as
    ``tasks_suspended``; those that fail checkpoint or have no saver
    are reported as ``tasks_interrupted``.
    """

    # Internal constants by design: cancellation propagation and
    # checkpoint persistence are bounded so total shutdown stays
    # within the lifecycle budget.  Longer values defer SIGKILL
    # past the orchestrator's grace period.  Not exposed to the
    # settings registry.
    _CANCEL_PROPAGATION_TIMEOUT: Final[float] = 5.0
    _CHECKPOINT_TIMEOUT: Final[float] = 30.0

    def __init__(
        self,
        *,
        grace_seconds: float = _DEFAULT_GRACE_SECONDS,
        cleanup_seconds: float = _DEFAULT_CLEANUP_SECONDS,
        checkpoint_saver: CheckpointSaver | None = None,
        clock: Clock | None = None,
    ) -> None:
        """Initialize the strategy.

        Args:
            grace_seconds: Cooperative-cancel window before stragglers
                are checkpointed and force-cancelled.  Must be positive.
            cleanup_seconds: Grace window for the post-cancel cleanup
                callbacks before they themselves are timed out.  Must
                be positive.
            checkpoint_saver: Optional callback that persists a task's
                resumable state when it is checkpointed.  When ``None``
                tasks beyond the grace window are reported as
                ``tasks_interrupted`` rather than ``tasks_suspended``.
            clock: Injectable time source; defaults to ``SystemClock``.

        Raises:
            ValueError: If ``grace_seconds`` or ``cleanup_seconds`` is
                non-positive.
        """
        if grace_seconds <= 0:
            msg = f"grace_seconds must be positive, got {grace_seconds}"
            logger.warning(
                EXECUTION_SHUTDOWN_TASK_ERROR,
                error=msg,
                param="grace_seconds",
                value=grace_seconds,
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
        self._grace_seconds = grace_seconds
        self._cleanup_seconds = cleanup_seconds
        self._checkpoint_saver = checkpoint_saver
        self._shutdown_event = asyncio.Event()

    def request_shutdown(self) -> None:
        """Signal that a graceful shutdown has been requested."""
        self._shutdown_event.set()

    def is_shutting_down(self) -> bool:
        """Return ``True`` when shutdown has been requested."""
        return self._shutdown_event.is_set()

    def get_strategy_type(self) -> str:
        """Return the strategy identifier."""
        return "checkpoint"

    async def execute_shutdown(
        self,
        *,
        running_tasks: Mapping[str, asyncio.Task[object]],
        cleanup_callbacks: Sequence[CleanupCallback],
    ) -> ShutdownResult:
        """Checkpoint tasks, then stop.

        Returns:
            A :class:`ShutdownResult` carrying ``tasks_suspended``
            (cooperative + checkpointed stragglers) and
            ``tasks_interrupted`` (errored + uncheckpointable
            stragglers).
        """
        start = self._clock.monotonic()
        self._shutdown_event.set()

        logger.info(
            EXECUTION_SHUTDOWN_GRACE_START,
            grace_seconds=self._grace_seconds,
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
                tasks_suspended=0,
                cleanup_completed=cleanup_completed,
                duration_seconds=self._clock.monotonic() - start,
            )
            logger.info(
                EXECUTION_SHUTDOWN_COMPLETE,
                strategy=result.strategy_type,
                tasks_suspended=0,
                tasks_interrupted=0,
                cleanup_completed=result.cleanup_completed,
                duration_seconds=result.duration_seconds,
            )
            return result

        task_set = set(running_tasks.values())
        done, pending = await asyncio.wait(
            task_set,
            timeout=self._grace_seconds,
        )

        # Cooperative exits counted as suspended; errored tasks
        # are counted as interrupted (they need attention on restart).
        tasks_suspended, tasks_errored = _count_cooperative_exits(done)

        # Checkpoint and cancel stragglers.
        (
            straggler_suspended,
            tasks_interrupted,
        ) = await self._checkpoint_and_cancel_pending(
            pending,
            running_tasks,
        )
        tasks_suspended += straggler_suspended
        tasks_interrupted += tasks_errored

        cleanup_completed = await _run_cleanup(
            cleanup_callbacks,
            self._cleanup_seconds,
        )

        result = ShutdownResult(
            strategy_type=self.get_strategy_type(),
            tasks_interrupted=tasks_interrupted,
            tasks_completed=0,
            tasks_suspended=tasks_suspended,
            cleanup_completed=cleanup_completed,
            duration_seconds=self._clock.monotonic() - start,
        )
        logger.info(
            EXECUTION_SHUTDOWN_COMPLETE,
            strategy=result.strategy_type,
            tasks_suspended=result.tasks_suspended,
            tasks_interrupted=result.tasks_interrupted,
            cleanup_completed=result.cleanup_completed,
            duration_seconds=result.duration_seconds,
        )
        return result

    async def _checkpoint_and_cancel_pending(
        self,
        pending: set[asyncio.Task[object]],
        running_tasks: Mapping[str, asyncio.Task[object]],
    ) -> tuple[int, int]:
        """Checkpoint straggler tasks concurrently, then cancel.

        Uses ``asyncio.TaskGroup`` to fan out checkpoint attempts
        for all stragglers in parallel.

        Returns:
            Tuple of (tasks_suspended, tasks_interrupted).
        """
        if not pending:
            return 0, 0

        task_to_id = {t: tid for tid, t in running_tasks.items()}
        tasks_suspended = 0
        tasks_interrupted = 0

        # Identify tasks with valid IDs vs unknown.
        checkpointable: list[tuple[asyncio.Task[object], str]] = []
        for task in pending:
            task_id = task_to_id.get(task)
            if task_id is None:
                logger.warning(
                    EXECUTION_SHUTDOWN_TASK_ERROR,
                    error="Task not found in reverse map during checkpoint",
                )
                tasks_interrupted += 1
                task.cancel()
            else:
                checkpointable.append((task, task_id))

        # Fan out checkpoint attempts concurrently.
        if checkpointable:

            async def _checkpoint_one(tid: str) -> bool:
                return await self._try_checkpoint(tid)

            async with asyncio.TaskGroup() as tg:
                checkpoint_tasks = [
                    tg.create_task(_checkpoint_one(tid)) for _, tid in checkpointable
                ]

            for (task, _), ct in zip(
                checkpointable,
                checkpoint_tasks,
                strict=True,
            ):
                saved = ct.result()
                if saved:
                    tasks_suspended += 1
                else:
                    tasks_interrupted += 1
                task.cancel()

        # Wait for cancellation to propagate.
        cancel_done, _ = await asyncio.wait(
            pending,
            timeout=self._CANCEL_PROPAGATION_TIMEOUT,
        )
        _log_post_cancel_exceptions(cancel_done)

        return tasks_suspended, tasks_interrupted

    async def _try_checkpoint(self, task_id: str) -> bool:
        """Attempt to save a checkpoint for the given task.

        The saver call is bounded by ``_CHECKPOINT_TIMEOUT`` to
        prevent hangs from blocking shutdown indefinitely.

        Returns:
            ``True`` if checkpoint was saved, ``False`` otherwise.
        """
        if self._checkpoint_saver is None:
            return False
        try:
            saved = await asyncio.wait_for(
                self._checkpoint_saver(task_id),
                timeout=self._CHECKPOINT_TIMEOUT,
            )
        except TimeoutError:
            logger.warning(
                EXECUTION_SHUTDOWN_CHECKPOINT_FAILED,
                task_id=task_id,
                reason="checkpoint timed out",
            )
            return False
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                EXECUTION_SHUTDOWN_CHECKPOINT_FAILED,
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        if saved:
            logger.info(
                EXECUTION_SHUTDOWN_CHECKPOINT_SAVE,
                task_id=task_id,
            )
        else:
            logger.warning(
                EXECUTION_SHUTDOWN_CHECKPOINT_FAILED,
                task_id=task_id,
                reason="saver returned False",
            )
        return saved
