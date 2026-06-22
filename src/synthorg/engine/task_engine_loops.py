"""Background loop and drain helpers for :class:`TaskEngine`.

Mixin extracted from :mod:`synthorg.engine.task_engine` to keep the
main module under the project size limit.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.task_engine_apply import dispatch as _dispatch_mutation
from synthorg.engine.task_engine_config import TaskEngineConfig
from synthorg.engine.task_engine_events import (
    build_state_changed_event,
    publish_snapshot,
)
from synthorg.engine.task_engine_models import (
    TaskMutation,
    TaskMutationResult,
    TaskStateChanged,
)
from synthorg.engine.task_engine_version import (
    TaskSpanTracker,
    TaskTimingTracker,
    VersionTracker,
)
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.task_engine import (
    TASK_ENGINE_DRAIN_COMPLETE,
    TASK_ENGINE_DRAIN_START,
    TASK_ENGINE_DRAIN_TIMEOUT,
    TASK_ENGINE_FUTURES_FAILED,
    TASK_ENGINE_LOOP_ERROR,
    TASK_ENGINE_MUTATION_APPLIED,
    TASK_ENGINE_MUTATION_FAILED,
    TASK_ENGINE_MUTATION_RECEIVED,
    TASK_ENGINE_OBSERVER_DRAIN_TIMEOUT,
    TASK_ENGINE_OBSERVER_FAILED,
    TASK_ENGINE_OBSERVER_QUEUE_FULL,
)

if TYPE_CHECKING:
    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)

# After this many CONSECUTIVE ``_process_one`` failures the processing
# loop escalates from per-mutation WARNING to a loud ERROR carrying the
# run length, so a programming error that turns every mutation into an
# ``internal`` response is alertable instead of an invisible stream of
# redacted warnings.
_MAX_CONSECUTIVE_PROCESSING_FAILURES: Final[int] = 5


@dataclass
class _MutationEnvelope:
    """Pairs a mutation request with its response future."""

    mutation: TaskMutation
    future: asyncio.Future[TaskMutationResult] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future(),
    )


class TaskEngineLoopsMixin:
    """Mixin providing background processing and drain helpers."""

    _SNAPSHOT_SENDER: str
    _SNAPSHOT_CHANNEL: str
    _POLL_INTERVAL_SECONDS: float

    _queue: asyncio.Queue[_MutationEnvelope]
    _observer_queue: asyncio.Queue[TaskStateChanged | None]
    _observers: list[Callable[[TaskStateChanged], Awaitable[None]]]
    _in_flight: _MutationEnvelope | None
    _running: bool
    _processing_task: asyncio.Task[None] | None
    _observer_task: asyncio.Task[None] | None
    _persistence: PersistenceBackend
    _versions: VersionTracker
    _timings: TaskTimingTracker
    _spans: TaskSpanTracker
    _clock: Clock
    _message_bus: MessageBus | None
    _config: TaskEngineConfig

    async def _drain_processing(self, budget: float) -> None:
        """Drain the mutation processing loop within *budget* seconds."""
        if self._processing_task is None:
            return
        logger.info(
            TASK_ENGINE_DRAIN_START,
            pending=self._queue.qsize(),
            timeout_seconds=budget,
        )
        try:
            await asyncio.wait_for(
                asyncio.shield(self._processing_task),
                timeout=budget,
            )
            logger.info(TASK_ENGINE_DRAIN_COMPLETE)
        except TimeoutError:
            logger.warning(
                TASK_ENGINE_DRAIN_TIMEOUT,
                remaining=self._queue.qsize(),
            )
            saved_in_flight = self._in_flight
            self._processing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._processing_task
            self._fail_remaining_futures(saved_in_flight)
        except BaseException:
            self._fail_remaining_futures(self._in_flight)
            # Consume the exception from the underlying processing
            # task so asyncio does not emit "Future exception was
            # never retrieved" when the Future is garbage-collected.
            # The exception is being re-raised below; this only
            # marks it as retrieved on the original Task object
            # (``asyncio.shield`` may have created an inner wrapper
            # whose ``await`` retrieval did not propagate the
            # ``_log_traceback`` clearing back to the wrapped task --
            # observed under MemoryError / RecursionError dispatch
            # paths in ``test_task_engine_extended.py``).
            if self._processing_task.done():
                with contextlib.suppress(BaseException):
                    self._processing_task.exception()
            raise
        finally:
            self._processing_task = None

    async def _drain_observer(self, budget: float) -> None:
        """Drain the observer dispatch loop within *budget* seconds."""
        if self._observer_task is None:
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(self._observer_task),
                timeout=budget,
            )
        except TimeoutError:
            logger.warning(
                TASK_ENGINE_OBSERVER_DRAIN_TIMEOUT,
                remaining=self._observer_queue.qsize(),
            )
            self._observer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._observer_task
        finally:
            self._observer_task = None

    def _fail_remaining_futures(
        self,
        saved_in_flight: _MutationEnvelope | None = None,
    ) -> None:
        """Fail in-flight and queued futures after drain timeout."""
        shutdown_result_for = self._shutdown_result
        failed_count = 0
        in_flight = saved_in_flight if saved_in_flight is not None else self._in_flight
        if in_flight is not None and not in_flight.future.done():
            in_flight.future.set_result(shutdown_result_for(in_flight))
            failed_count += 1
        self._in_flight = None
        while not self._queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                envelope = self._queue.get_nowait()
                if not envelope.future.done():
                    envelope.future.set_result(shutdown_result_for(envelope))
                    failed_count += 1
        if failed_count:
            logger.warning(
                TASK_ENGINE_FUTURES_FAILED,
                failed_futures=failed_count,
                note="Resolved remaining futures with shutdown failure",
            )

    @staticmethod
    def _shutdown_result(envelope: _MutationEnvelope) -> TaskMutationResult:
        """Build an internal-failure result for a shutdown-aborted envelope.

        Returns:
            A :class:`TaskMutationResult` marked unsuccessful with
            ``error_code='internal'`` and a shutdown-specific message.
        """
        return TaskMutationResult(
            request_id=envelope.mutation.request_id,
            success=False,
            error="TaskEngine shut down before processing",
            error_code="internal",
        )

    async def _processing_loop(self) -> None:
        """Background loop: dequeue and process mutations sequentially.

        Continues draining queued mutations after ``_running`` is set to
        ``False``, enabling graceful shutdown.

        Raises:
            MemoryError: Re-raised from the SKIP log-and-raise path so
                the worker process surfaces the interpreter-critical
                failure.
            RecursionError: Same path as ``MemoryError``.
        """
        consecutive_failures = 0
        while self._running or not self._queue.empty():
            try:
                envelope = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=self._POLL_INTERVAL_SECONDS,
                )
            except TimeoutError:
                continue
            try:
                await self._process_one(envelope)
                consecutive_failures = 0
            except (MemoryError, RecursionError) as exc:
                if not envelope.future.done():
                    envelope.future.set_exception(exc)
                    # Mark the exception as retrieved on the
                    # envelope future. The caller awaiting that
                    # future may have already unwound (the
                    # processing task is about to re-raise this
                    # fatal error and abort drain), so without an
                    # explicit retrieval asyncio emits "Future
                    # exception was never retrieved" when the
                    # envelope is garbage-collected. The exception
                    # itself is being re-raised below; we only need
                    # to clear the Future's _log_traceback flag.
                    with contextlib.suppress(BaseException):
                        envelope.future.exception()
                raise
            except Exception as exc:  # noqa: BLE001 -- processing-loop boundary
                consecutive_failures += 1
                log_exception_redacted(
                    logger,
                    TASK_ENGINE_LOOP_ERROR,
                    exc,
                    reason="Unhandled exception in processing loop",
                    consecutive_failure_count=consecutive_failures,
                )
                if consecutive_failures >= _MAX_CONSECUTIVE_PROCESSING_FAILURES:
                    # A sustained run of failures means the loop is wedged
                    # converting every mutation into an ``internal`` error
                    # rather than a one-off bad request. Escalate to ERROR
                    # so monitoring can alert; the loop keeps draining so
                    # queued callers still get a (failed) response instead
                    # of hanging forever on an aborted loop.
                    logger.error(
                        TASK_ENGINE_LOOP_ERROR,
                        reason="processing loop wedged on consecutive failures",
                        consecutive_failure_count=consecutive_failures,
                        threshold=_MAX_CONSECUTIVE_PROCESSING_FAILURES,
                    )
                if not envelope.future.done():
                    envelope.future.set_result(
                        TaskMutationResult(
                            request_id=envelope.mutation.request_id,
                            success=False,
                            error="Internal error in processing loop",
                            error_code="internal",
                        ),
                    )

    async def _process_one(self, envelope: _MutationEnvelope) -> None:
        """Process a single mutation envelope."""
        mutation = envelope.mutation
        self._in_flight = envelope
        logger.debug(
            TASK_ENGINE_MUTATION_RECEIVED,
            mutation_type=mutation.mutation_type,
            request_id=mutation.request_id,
        )
        try:
            result = await _dispatch_mutation(
                mutation,
                self._persistence,
                self._versions,
                self._timings,
                self._spans,
                clock=self._clock,
            )
            if not envelope.future.done():
                envelope.future.set_result(result)
            if result.success:
                task_id = getattr(mutation, "task_id", None)
                logger.info(
                    TASK_ENGINE_MUTATION_APPLIED,
                    mutation_type=mutation.mutation_type,
                    request_id=mutation.request_id,
                    task_id=task_id or (result.task.id if result.task else None),
                    version=result.version,
                    previous_status=(
                        result.previous_status.value if result.previous_status else None
                    ),
                    new_status=(result.task.status.value if result.task else None),
                )
            if result.success and (self._config.publish_snapshots or self._observers):
                event = build_state_changed_event(mutation, result)
                if self._config.publish_snapshots:
                    await publish_snapshot(
                        mutation,
                        event,
                        message_bus=self._message_bus,
                        sender=self._SNAPSHOT_SENDER,
                        channel=self._SNAPSHOT_CHANNEL,
                    )
                if self._observers:
                    try:
                        self._observer_queue.put_nowait(event)
                    except asyncio.QueueFull:
                        logger.warning(
                            TASK_ENGINE_OBSERVER_QUEUE_FULL,
                            request_id=event.request_id,
                            task_id=event.task_id,
                            mutation_type=event.mutation_type,
                            queue_size=self._observer_queue.qsize(),
                        )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                TASK_ENGINE_MUTATION_FAILED,
                exc,
                mutation_type=mutation.mutation_type,
                request_id=mutation.request_id,
            )
            if not envelope.future.done():
                envelope.future.set_result(
                    TaskMutationResult(
                        request_id=mutation.request_id,
                        success=False,
                        error="Internal error processing mutation",
                        error_code="internal",
                    ),
                )
        finally:
            self._in_flight = None

    async def _observer_dispatch_loop(self) -> None:
        """Background loop: dequeue and dispatch observer events."""
        # lint-allow: long-running-loop-kill-switch -- stop() sentinel drains queue.
        while True:
            try:
                event = await asyncio.wait_for(
                    self._observer_queue.get(),
                    timeout=self._POLL_INTERVAL_SECONDS,
                )
            except TimeoutError:
                continue
            if event is None:
                self._observer_queue.task_done()
                break
            try:
                await self._notify_observers(event)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                log_exception_redacted(
                    logger,
                    TASK_ENGINE_LOOP_ERROR,
                    exc,
                    reason="Unhandled exception in observer dispatch loop",
                    task_id=event.task_id,
                    request_id=event.request_id,
                    mutation_type=event.mutation_type,
                )
            finally:
                self._observer_queue.task_done()

    async def _notify_observers(
        self,
        event: TaskStateChanged,
    ) -> None:
        """Notify registered observers of a successful mutation."""
        for observer in self._observers:
            try:
                await observer(event)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    TASK_ENGINE_OBSERVER_FAILED,
                    observer=getattr(observer, "__qualname__", repr(observer)),
                    mutation_type=event.mutation_type,
                    request_id=event.request_id,
                    task_id=event.task_id,
                )
