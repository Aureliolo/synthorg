"""Graceful shutdown strategy and manager.

Implements DESIGN_SPEC §6.7 — cooperative timeout strategy for clean
process shutdown.  When SIGINT/SIGTERM is received the framework signals
agents to exit at turn boundaries, waits a grace period, force-cancels
stragglers, and runs cleanup callbacks.  The *engine* layer is responsible
for transitioning tasks to INTERRUPTED (see ``AgentEngine``).

The ``ShutdownStrategy`` protocol is pluggable for future strategies.
"""

import asyncio
import contextlib
import signal
import sys
import time
import types  # noqa: TC003 — used in runtime-visible annotation
from collections.abc import Callable, Coroutine, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ai_company.core.types import NotBlankStr  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.execution import (
    EXECUTION_SHUTDOWN_CLEANUP,
    EXECUTION_SHUTDOWN_CLEANUP_FAILED,
    EXECUTION_SHUTDOWN_CLEANUP_TIMEOUT,
    EXECUTION_SHUTDOWN_COMPLETE,
    EXECUTION_SHUTDOWN_FORCE_CANCEL,
    EXECUTION_SHUTDOWN_GRACE_START,
    EXECUTION_SHUTDOWN_MANAGER_CREATED,
    EXECUTION_SHUTDOWN_SIGNAL,
    EXECUTION_SHUTDOWN_TASK_ERROR,
    EXECUTION_SHUTDOWN_TASK_TRACKED,
)

logger = get_logger(__name__)

CleanupCallback = Callable[[], Coroutine[Any, Any, None]]
"""Async callback invoked during shutdown cleanup phase."""


class ShutdownResult(BaseModel):
    """Outcome of a graceful shutdown sequence.

    Attributes:
        strategy_type: Name of the strategy that executed the shutdown.
        tasks_interrupted: Number of tasks that were force-cancelled.
        tasks_completed: Number of tasks that exited cooperatively.
        cleanup_completed: Whether all cleanup callbacks finished
            within the allowed time.
        duration_seconds: Wall-clock duration of the entire shutdown.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    strategy_type: NotBlankStr = Field(
        description="Name of the strategy that executed the shutdown",
    )
    tasks_interrupted: int = Field(
        ge=0,
        description=(
            "Number of tasks still running after the grace period "
            "that were force-cancelled"
        ),
    )
    tasks_completed: int = Field(
        ge=0,
        description="Number of tasks that exited cooperatively",
    )
    cleanup_completed: bool = Field(
        description="Whether all cleanup callbacks finished in time",
    )
    duration_seconds: float = Field(
        ge=0.0,
        description="Wall-clock duration of the shutdown sequence",
    )


@runtime_checkable
class ShutdownStrategy(Protocol):
    """Protocol for pluggable shutdown strategies."""

    def request_shutdown(self) -> None:
        """Signal that a graceful shutdown has been requested."""
        ...

    def is_shutting_down(self) -> bool:
        """Return ``True`` when shutdown has been requested."""
        ...

    async def execute_shutdown(
        self,
        *,
        running_tasks: Mapping[str, asyncio.Task[Any]],
        cleanup_callbacks: Sequence[CleanupCallback],
    ) -> ShutdownResult:
        """Execute the full shutdown sequence.

        Args:
            running_tasks: Map of task_id → asyncio.Task for in-flight
                agent executions.
            cleanup_callbacks: Ordered sequence of async cleanup callbacks
                to invoke after task shutdown.

        Returns:
            Outcome of the shutdown sequence.
        """
        ...

    def get_strategy_type(self) -> str:
        """Return the strategy identifier (e.g. ``"cooperative_timeout"``)."""
        ...


class CooperativeTimeoutStrategy:
    """Cooperative timeout shutdown strategy.

    1. Set shutdown event (signal agents via turn-boundary checks).
    2. Wait up to ``grace_seconds`` for tasks to exit cooperatively.
    3. Force-cancel any remaining tasks.
    4. Run cleanup callbacks within ``cleanup_seconds``.
    """

    def __init__(
        self,
        *,
        grace_seconds: float = 30.0,
        cleanup_seconds: float = 5.0,
    ) -> None:
        if grace_seconds <= 0:
            msg = f"grace_seconds must be positive, got {grace_seconds}"
            raise ValueError(msg)
        if cleanup_seconds <= 0:
            msg = f"cleanup_seconds must be positive, got {cleanup_seconds}"
            raise ValueError(msg)
        self._grace_seconds = grace_seconds
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
        return "cooperative_timeout"

    async def execute_shutdown(
        self,
        *,
        running_tasks: Mapping[str, asyncio.Task[Any]],
        cleanup_callbacks: Sequence[CleanupCallback],
    ) -> ShutdownResult:
        """Execute the cooperative timeout shutdown sequence."""
        start = time.monotonic()

        self._shutdown_event.set()
        logger.info(
            EXECUTION_SHUTDOWN_GRACE_START,
            grace_seconds=self._grace_seconds,
            running_tasks=len(running_tasks),
        )

        tasks_completed, tasks_interrupted = await self._wait_and_cancel(
            running_tasks,
        )

        cleanup_completed = await self._run_cleanup(cleanup_callbacks)

        duration = time.monotonic() - start
        result = ShutdownResult(
            strategy_type=self.get_strategy_type(),
            tasks_interrupted=tasks_interrupted,
            tasks_completed=tasks_completed,
            cleanup_completed=cleanup_completed,
            duration_seconds=duration,
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

    _CANCEL_PROPAGATION_TIMEOUT: float = 5.0
    """Seconds to wait for cancellation to propagate after force-cancel."""

    async def _wait_and_cancel(
        self,
        running_tasks: Mapping[str, asyncio.Task[Any]],
    ) -> tuple[int, int]:
        """Wait for cooperative exit, then force-cancel stragglers.

        Returns:
            Tuple of (tasks_completed, tasks_interrupted).
        """
        if not running_tasks:
            return 0, 0

        task_set = set(running_tasks.values())
        done, pending = await asyncio.wait(
            task_set,
            timeout=self._grace_seconds,
        )

        # Retrieve exceptions from done tasks to prevent
        # "Task exception was never retrieved" warnings.
        # Tasks that raised are not counted as "completed" — only
        # cleanly-finished tasks count.
        tasks_completed = 0
        for task in done:
            if task.cancelled():
                continue
            exc = task.exception()
            if exc is not None:
                logger.warning(
                    EXECUTION_SHUTDOWN_TASK_ERROR,
                    error=(f"Task raised during shutdown: {type(exc).__name__}"),
                )
            else:
                tasks_completed += 1

        if pending:
            logger.warning(
                EXECUTION_SHUTDOWN_FORCE_CANCEL,
                pending_tasks=len(pending),
            )
            for task in pending:
                task.cancel()
            # Wait for cancellation to propagate (bounded).
            # Retrieve exceptions to suppress "never retrieved" warnings.
            cancel_done, _ = await asyncio.wait(
                pending,
                timeout=self._CANCEL_PROPAGATION_TIMEOUT,
            )
            self._log_post_cancel_exceptions(cancel_done)

        return tasks_completed, len(pending)

    def _log_post_cancel_exceptions(
        self,
        tasks: set[asyncio.Task[Any]],
    ) -> None:
        """Retrieve and log exceptions from post-cancel tasks.

        Retrieving the exception prevents asyncio's "Task exception was
        never retrieved" warning.  Non-cancelled tasks with exceptions
        are logged at DEBUG.
        """
        for task in tasks:
            if task.cancelled():
                continue
            try:
                exc = task.exception()
            except asyncio.InvalidStateError:
                logger.debug(
                    EXECUTION_SHUTDOWN_TASK_ERROR,
                    error="Failed to inspect post-cancel task: InvalidStateError",
                    task_name=task.get_name(),
                )
            else:
                if exc is not None:
                    logger.debug(
                        EXECUTION_SHUTDOWN_TASK_ERROR,
                        error=(
                            f"Post-cancel task exception: {type(exc).__name__}: {exc}"
                        ),
                        task_name=task.get_name(),
                    )

    async def _run_cleanup(
        self,
        callbacks: Sequence[CleanupCallback],
    ) -> bool:
        """Run cleanup callbacks sequentially within the time budget.

        Returns:
            ``True`` if all callbacks completed successfully within the
            time budget, ``False`` otherwise.
        """
        if not callbacks:
            return True

        logger.info(
            EXECUTION_SHUTDOWN_CLEANUP,
            callback_count=len(callbacks),
            cleanup_seconds=self._cleanup_seconds,
        )

        all_succeeded = True

        async def _run_all() -> None:
            nonlocal all_succeeded
            for i, callback in enumerate(callbacks):
                try:
                    await callback()
                except Exception:
                    all_succeeded = False
                    logger.exception(
                        EXECUTION_SHUTDOWN_CLEANUP_FAILED,
                        callback_index=i,
                        callback_count=len(callbacks),
                    )

        try:
            await asyncio.wait_for(
                _run_all(),
                timeout=self._cleanup_seconds,
            )
        except TimeoutError:
            logger.warning(
                EXECUTION_SHUTDOWN_CLEANUP_TIMEOUT,
                cleanup_seconds=self._cleanup_seconds,
            )
            return False
        return all_succeeded


class ShutdownManager:
    """Manages signal handling, task tracking, and shutdown orchestration.

    Separates OS signal handling from shutdown strategy logic.

    Args:
        strategy: Shutdown strategy implementation.  Defaults to
            ``CooperativeTimeoutStrategy()``.
    """

    def __init__(
        self,
        strategy: ShutdownStrategy | None = None,
    ) -> None:
        self._strategy: ShutdownStrategy = strategy or CooperativeTimeoutStrategy()
        self._running_tasks: dict[str, asyncio.Task[Any]] = {}
        self._cleanup_callbacks: list[CleanupCallback] = []
        self._signals_installed = False
        logger.debug(
            EXECUTION_SHUTDOWN_MANAGER_CREATED,
            strategy=self._strategy.get_strategy_type(),
        )

    @property
    def strategy(self) -> ShutdownStrategy:
        """The configured shutdown strategy."""
        return self._strategy

    def install_signal_handlers(self) -> None:
        """Install SIGINT/SIGTERM handlers.

        On Unix uses ``loop.add_signal_handler``.
        On Windows uses ``signal.signal`` with ``call_soon_threadsafe``.
        """
        if self._signals_installed:
            return

        if sys.platform != "win32":
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self._handle_signal, sig)
        else:
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, self._handle_signal_threadsafe)

        self._signals_installed = True

    def _handle_signal(self, sig: signal.Signals) -> None:
        """Handle signal on Unix (called in event loop thread)."""
        logger.info(
            EXECUTION_SHUTDOWN_SIGNAL,
            signal=sig.name,
        )
        try:
            self._strategy.request_shutdown()
        except Exception:
            logger.exception(
                EXECUTION_SHUTDOWN_SIGNAL,
                signal=sig.name,
                error="request_shutdown() raised — falling back to loop.stop()",
            )
            # If request_shutdown() itself fails, stop the event loop as
            # a last resort to avoid a process that ignores signals.
            with contextlib.suppress(Exception):
                asyncio.get_running_loop().stop()

    def _handle_signal_threadsafe(
        self,
        signum: int,
        _frame: types.FrameType | None,
    ) -> None:
        """Handle signal on Windows (called outside the event loop context).

        Logging is deferred to the event loop via ``call_soon_threadsafe``
        to avoid deadlocks (structlog acquires locks internally).
        """
        try:
            sig_name = signal.Signals(signum).name
        except ValueError:
            sig_name = f"UNKNOWN({signum})"

        def _on_loop() -> None:
            try:
                logger.info(
                    EXECUTION_SHUTDOWN_SIGNAL,
                    signal=sig_name,
                )
                self._strategy.request_shutdown()
            except Exception:
                logger.exception(
                    EXECUTION_SHUTDOWN_SIGNAL,
                    signal=sig_name,
                    error="request_shutdown() raised — falling back to loop.stop()",
                )
                with contextlib.suppress(Exception):
                    asyncio.get_running_loop().stop()

        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(_on_loop)
        except RuntimeError:
            # No running event loop — call directly (best-effort).
            # Cannot use structlog (acquires locks) so fall back to
            # stderr for last-resort visibility.
            try:
                self._strategy.request_shutdown()
            except Exception:
                try:
                    sys.stderr.write(
                        f"[shutdown] request_shutdown() failed for signal {sig_name}\n"
                    )
                    sys.stderr.flush()
                except Exception:  # noqa: S110
                    pass

    def register_task(
        self,
        task_id: str,
        asyncio_task: asyncio.Task[Any],
    ) -> None:
        """Track a running agent task.

        Raises:
            RuntimeError: If shutdown has already been requested (drain
                gate is closed).
        """
        if self._strategy.is_shutting_down():
            msg = f"Cannot register task {task_id!r}: shutdown already in progress"
            raise RuntimeError(msg)
        if task_id in self._running_tasks:
            logger.warning(
                EXECUTION_SHUTDOWN_TASK_TRACKED,
                action="task_overwritten",
                task_id=task_id,
            )
        self._running_tasks[task_id] = asyncio_task
        logger.debug(
            EXECUTION_SHUTDOWN_TASK_TRACKED,
            action="task_registered",
            task_id=task_id,
            running_tasks=len(self._running_tasks),
        )

    def unregister_task(self, task_id: str) -> None:
        """Stop tracking a completed agent task."""
        self._running_tasks.pop(task_id, None)
        logger.debug(
            EXECUTION_SHUTDOWN_TASK_TRACKED,
            action="task_unregistered",
            task_id=task_id,
            running_tasks=len(self._running_tasks),
        )

    def register_cleanup(self, callback: CleanupCallback) -> None:
        """Register an async cleanup callback for shutdown.

        Callbacks run sequentially in registration order during
        shutdown.  Each callback is individually guarded against
        exceptions — a failing callback does not prevent subsequent
        ones from running.
        """
        self._cleanup_callbacks.append(callback)

    def is_shutting_down(self) -> bool:
        """Delegate to the strategy's shutdown check."""
        return self._strategy.is_shutting_down()

    async def initiate_shutdown(self) -> ShutdownResult:
        """Invoke the strategy's shutdown sequence."""
        return await self._strategy.execute_shutdown(
            running_tasks=dict(self._running_tasks),
            cleanup_callbacks=list(self._cleanup_callbacks),
        )
