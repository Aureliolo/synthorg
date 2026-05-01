"""Additional shutdown strategy implementations.

Provides ``ImmediateCancelStrategy``, ``FinishCurrentToolStrategy``,
``CheckpointAndStopStrategy``, and the ``build_shutdown_strategy``
factory.  All satisfy the ``ShutdownStrategy`` protocol defined in
``synthorg.engine.shutdown``.
"""

import asyncio
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from synthorg.config.schema import GracefulShutdownConfig

from synthorg.engine.shutdown import (
    CheckpointSaver,
    CleanupCallback,
    CooperativeTimeoutStrategy,
    ShutdownResult,
    ShutdownStrategy,
    _log_post_cancel_exceptions,
    _run_cleanup,
)
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_SHUTDOWN_CHECKPOINT_FAILED,
    EXECUTION_SHUTDOWN_CHECKPOINT_SAVE,
    EXECUTION_SHUTDOWN_COMPLETE,
    EXECUTION_SHUTDOWN_FORCE_CANCEL,
    EXECUTION_SHUTDOWN_GRACE_START,
    EXECUTION_SHUTDOWN_IMMEDIATE_CANCEL,
    EXECUTION_SHUTDOWN_TASK_ERROR,
    EXECUTION_SHUTDOWN_TOOL_WAIT,
)

logger = get_logger(__name__)


# ── Shared helpers ───────────────────────────────────────────────


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
                error=(f"Task raised during shutdown: {type(exc).__name__}: {exc}"),
            )
        else:
            completed += 1
    return completed, errored


# ── Strategy implementations ────────────────────────────────────


class ImmediateCancelStrategy:
    """Immediate cancel shutdown strategy.

    Force-cancel all agent tasks immediately with no grace period.
    Fastest shutdown but highest data loss -- partial tool side effects,
    billed-but-lost LLM responses.
    """

    def __init__(self, *, cleanup_seconds: float = 5.0) -> None:
        if cleanup_seconds <= 0:
            msg = f"cleanup_seconds must be positive, got {cleanup_seconds}"
            logger.warning(
                EXECUTION_SHUTDOWN_TASK_ERROR,
                error=msg,
                param="cleanup_seconds",
                value=cleanup_seconds,
            )
            raise ValueError(msg)
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
        return "immediate"

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
        """Cancel all tasks immediately, then run cleanup."""
        start = time.monotonic()
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
            duration_seconds=time.monotonic() - start,
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
        cleanup_seconds: float = 5.0,
    ) -> None:
        # ``tool_timeout_seconds`` is operator-tunable; resolve via
        # ``ConfigResolver.get_float("engine",
        # "shutdown_tool_timeout_seconds")`` at the call site.
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
        """Wait for current tool, then cancel stragglers."""
        start = time.monotonic()
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
                duration_seconds=time.monotonic() - start,
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
            duration_seconds=time.monotonic() - start,
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
    _CANCEL_PROPAGATION_TIMEOUT: float = 5.0
    _CHECKPOINT_TIMEOUT: float = 30.0

    def __init__(
        self,
        *,
        grace_seconds: float = 30.0,
        cleanup_seconds: float = 5.0,
        checkpoint_saver: CheckpointSaver | None = None,
    ) -> None:
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
        running_tasks: Mapping[str, asyncio.Task[Any]],
        cleanup_callbacks: Sequence[CleanupCallback],
    ) -> ShutdownResult:
        """Checkpoint tasks, then stop."""
        start = time.monotonic()
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
                duration_seconds=time.monotonic() - start,
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
            duration_seconds=time.monotonic() - start,
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
        pending: set[asyncio.Task[Any]],
        running_tasks: Mapping[str, asyncio.Task[Any]],
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
        checkpointable: list[tuple[asyncio.Task[Any], str]] = []
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
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.exception(
                EXECUTION_SHUTDOWN_CHECKPOINT_FAILED,
                task_id=task_id,
                error_type=type(exc).__name__,
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


# ── Factory ──────────────────────────────────────────────────────


def build_shutdown_strategy(
    config: GracefulShutdownConfig,
    *,
    checkpoint_saver: CheckpointSaver | None = None,
) -> ShutdownStrategy:
    """Build a shutdown strategy from configuration.

    Args:
        config: Shutdown configuration with strategy name and params.
        checkpoint_saver: Optional checkpoint callback for the
            ``"checkpoint"`` strategy.

    Returns:
        Configured shutdown strategy instance.

    Raises:
        ValueError: If ``config.strategy`` is not a known strategy
            name.
    """
    strategies: dict[str, Callable[[], ShutdownStrategy]] = {
        "cooperative_timeout": lambda: CooperativeTimeoutStrategy(
            grace_seconds=config.grace_seconds,
            cleanup_seconds=config.cleanup_seconds,
        ),
        "immediate": lambda: ImmediateCancelStrategy(
            cleanup_seconds=config.cleanup_seconds,
        ),
        "finish_tool": lambda: FinishCurrentToolStrategy(
            tool_timeout_seconds=config.tool_timeout_seconds,
            cleanup_seconds=config.cleanup_seconds,
        ),
        "checkpoint": lambda: CheckpointAndStopStrategy(
            grace_seconds=config.grace_seconds,
            cleanup_seconds=config.cleanup_seconds,
            checkpoint_saver=checkpoint_saver,
        ),
    }

    builder = strategies.get(config.strategy)
    if builder is None:
        msg = (
            f"Unknown shutdown strategy: {config.strategy!r}. "
            f"Known strategies: {sorted(strategies)}"
        )
        logger.warning(
            EXECUTION_SHUTDOWN_TASK_ERROR,
            error=msg,
            strategy=config.strategy,
            known_strategies=sorted(strategies),
        )
        raise ValueError(msg)

    return builder()
