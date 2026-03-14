"""Centralized single-writer task engine.

Owns all task state mutations via an ``asyncio.Queue``.  A single
background task consumes mutation requests sequentially, derives a new
``Task`` instance from the current state and the mutation (e.g. via
``Task.model_validate`` / ``Task.with_transition``), persists the result,
and publishes snapshots to the message bus.

Reads bypass the queue and go directly to persistence -- this is safe
because the TaskEngine is the only writer.
"""

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Never
from uuid import uuid4

from pydantic import ValidationError as PydanticValidationError

from synthorg.engine.errors import (
    TaskEngineNotRunningError,
    TaskEngineQueueFullError,
    TaskInternalError,
    TaskMutationError,
    TaskNotFoundError,
    TaskVersionConflictError,
)
from synthorg.engine.task_engine_apply import dispatch as _dispatch_mutation
from synthorg.engine.task_engine_config import TaskEngineConfig
from synthorg.engine.task_engine_models import (
    CancelTaskMutation,
    CreateTaskData,
    CreateTaskMutation,
    DeleteTaskMutation,
    TaskMutation,
    TaskMutationResult,
    TaskStateChanged,
    TransitionTaskMutation,
    UpdateTaskMutation,
)
from synthorg.engine.task_engine_version import VersionTracker
from synthorg.observability import get_logger
from synthorg.observability.events.task_engine import (
    TASK_ENGINE_CREATED,
    TASK_ENGINE_DRAIN_COMPLETE,
    TASK_ENGINE_DRAIN_START,
    TASK_ENGINE_DRAIN_TIMEOUT,
    TASK_ENGINE_FUTURES_FAILED,
    TASK_ENGINE_LIST_CAPPED,
    TASK_ENGINE_LOOP_ERROR,
    TASK_ENGINE_MUTATION_APPLIED,
    TASK_ENGINE_MUTATION_FAILED,
    TASK_ENGINE_MUTATION_RECEIVED,
    TASK_ENGINE_NOT_RUNNING,
    TASK_ENGINE_QUEUE_FULL,
    TASK_ENGINE_READ_FAILED,
    TASK_ENGINE_SNAPSHOT_PUBLISH_FAILED,
    TASK_ENGINE_SNAPSHOT_PUBLISHED,
    TASK_ENGINE_STARTED,
    TASK_ENGINE_STOPPED,
)

if TYPE_CHECKING:
    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.core.enums import TaskStatus
    from synthorg.core.task import Task
    from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)


@dataclass
class _MutationEnvelope:
    """Pairs a mutation request with its response future.

    Note: must be instantiated within a running event loop (the
    ``future`` default factory calls ``asyncio.get_running_loop()``).
    """

    mutation: TaskMutation
    future: asyncio.Future[TaskMutationResult] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future(),
    )


class TaskEngine:
    """Centralized single-writer for all task state mutations.

    Uses an actor-like pattern: a single background ``asyncio.Task``
    consumes ``TaskMutation`` requests from an ``asyncio.Queue``,
    applies each mutation sequentially, persists the result, and
    publishes state-change snapshots.

    Args:
        persistence: Backend for task storage.
        message_bus: Optional bus for snapshot publication.
        config: Engine configuration.
    """

    def __init__(
        self,
        *,
        persistence: PersistenceBackend,
        message_bus: MessageBus | None = None,
        config: TaskEngineConfig | None = None,
    ) -> None:
        self._persistence = persistence
        self._message_bus = message_bus
        self._config = config or TaskEngineConfig()
        self._queue: asyncio.Queue[_MutationEnvelope] = asyncio.Queue(
            maxsize=self._config.max_queue_size,
        )
        self._versions = VersionTracker()
        self._processing_task: asyncio.Task[None] | None = None
        self._in_flight: _MutationEnvelope | None = None
        self._running = False
        self._lifecycle_lock = asyncio.Lock()
        logger.debug(
            TASK_ENGINE_CREATED,
            max_queue_size=self._config.max_queue_size,
            publish_snapshots=self._config.publish_snapshots,
        )

    # -- Lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Spawn the background processing loop.

        Raises:
            RuntimeError: If already running.
        """
        if self._running:
            msg = "TaskEngine is already running"
            logger.warning(TASK_ENGINE_STARTED, error=msg)
            raise RuntimeError(msg)
        self._running = True
        self._processing_task = asyncio.create_task(
            self._processing_loop(),
            name="task-engine-loop",
        )
        logger.info(
            TASK_ENGINE_STARTED,
            max_queue_size=self._config.max_queue_size,
        )

    async def stop(self, *, timeout: float | None = None) -> None:  # noqa: ASYNC109
        """Stop the engine and drain pending mutations.

        Acquires ``_lifecycle_lock`` to prevent a race with ``submit()``
        where an envelope is enqueued after the processing loop exits.

        Args:
            timeout: Seconds to wait for drain.  Defaults to
                ``config.drain_timeout_seconds``.
        """
        async with self._lifecycle_lock:
            if not self._running:
                return
            self._running = False
        effective_timeout = (
            timeout if timeout is not None else self._config.drain_timeout_seconds
        )

        if self._processing_task is not None:
            logger.info(
                TASK_ENGINE_DRAIN_START,
                pending=self._queue.qsize(),
                timeout_seconds=effective_timeout,
            )
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._processing_task),
                    timeout=effective_timeout,
                )
                logger.info(TASK_ENGINE_DRAIN_COMPLETE)
            except TimeoutError:
                logger.warning(
                    TASK_ENGINE_DRAIN_TIMEOUT,
                    remaining=self._queue.qsize(),
                )
                # Capture in-flight ref before cancel — the finally block
                # in _process_one clears self._in_flight on CancelledError.
                saved_in_flight = self._in_flight
                self._processing_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._processing_task
                self._fail_remaining_futures(saved_in_flight)
            except BaseException:
                self._fail_remaining_futures(self._in_flight)
                raise
            finally:
                self._processing_task = None

        logger.info(TASK_ENGINE_STOPPED)

    def _fail_remaining_futures(
        self,
        saved_in_flight: _MutationEnvelope | None = None,
    ) -> None:
        """Fail in-flight and remaining enqueued futures after drain timeout.

        Args:
            saved_in_flight: In-flight envelope captured before task
                cancellation — needed because ``_process_one``'s
                ``finally`` block clears ``self._in_flight`` on
                ``CancelledError``.
        """
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
        """Build an internal-failure result for a shutdown-aborted envelope."""
        return TaskMutationResult(
            request_id=envelope.mutation.request_id,
            success=False,
            error="TaskEngine shut down before processing",
            error_code="internal",
        )

    @property
    def is_running(self) -> bool:
        """Whether the engine is accepting mutations."""
        return self._running

    # -- Submit & convenience methods --------------------------------------

    async def submit(self, mutation: TaskMutation) -> TaskMutationResult:
        """Submit a mutation and await its result.

        Acquires ``_lifecycle_lock`` to prevent a race between
        ``submit()`` and ``stop()`` where an envelope could be enqueued
        after the processing loop has already drained and exited.

        Args:
            mutation: The mutation to apply.

        Returns:
            Result of the mutation.

        Raises:
            TaskEngineNotRunningError: If the engine is not running.
            TaskEngineQueueFullError: If the queue is at capacity.
        """
        async with self._lifecycle_lock:
            if not self._running:
                logger.warning(
                    TASK_ENGINE_NOT_RUNNING,
                    mutation_type=mutation.mutation_type,
                    request_id=mutation.request_id,
                )
                msg = "TaskEngine is not running"
                raise TaskEngineNotRunningError(msg)

            envelope = _MutationEnvelope(mutation=mutation)
            try:
                self._queue.put_nowait(envelope)
            except asyncio.QueueFull:
                logger.warning(
                    TASK_ENGINE_QUEUE_FULL,
                    mutation_type=mutation.mutation_type,
                    request_id=mutation.request_id,
                    queue_size=self._queue.qsize(),
                )
                msg = "TaskEngine queue is full"
                raise TaskEngineQueueFullError(msg) from None

        return await envelope.future

    async def create_task(
        self,
        data: CreateTaskData,
        *,
        requested_by: str,
    ) -> Task:
        """Convenience: create a task and return the created Task.

        Args:
            data: Task creation data.
            requested_by: Identity of the requester.

        Returns:
            The created task.

        Raises:
            TaskEngineNotRunningError: If the engine is not running.
            TaskEngineQueueFullError: If the queue is at capacity.
            TaskMutationError: If the mutation fails.
        """
        try:
            mutation = CreateTaskMutation(
                request_id=uuid4().hex,
                requested_by=requested_by,
                task_data=data,
            )
        except PydanticValidationError as exc:
            raise TaskMutationError(str(exc)) from exc
        result = await self.submit(mutation)
        if not result.success:
            self._raise_typed_error(result)
        if result.task is None:
            msg = "Internal error: create succeeded but task is None"
            raise TaskInternalError(msg)
        return result.task

    async def update_task(
        self,
        task_id: str,
        updates: dict[str, object],
        *,
        requested_by: str,
        expected_version: int | None = None,
    ) -> Task:
        """Convenience: update task fields and return the updated Task.

        Args:
            task_id: Target task identifier.
            updates: Field-value pairs to apply.
            requested_by: Identity of the requester.
            expected_version: Optional optimistic concurrency version.

        Returns:
            The updated task.

        Raises:
            TaskEngineNotRunningError: If the engine is not running.
            TaskEngineQueueFullError: If the queue is at capacity.
            TaskNotFoundError: If the task is not found.
            TaskVersionConflictError: If ``expected_version`` doesn't match.
            TaskMutationError: If the mutation fails.
        """
        try:
            mutation = UpdateTaskMutation(
                request_id=uuid4().hex,
                requested_by=requested_by,
                task_id=task_id,
                updates=updates,
                expected_version=expected_version,
            )
        except PydanticValidationError as exc:
            raise TaskMutationError(str(exc)) from exc
        result = await self.submit(mutation)
        if not result.success:
            self._raise_typed_error(result)
        if result.task is None:
            msg = "Internal error: update succeeded but task is None"
            raise TaskInternalError(msg)
        return result.task

    async def transition_task(
        self,
        task_id: str,
        target_status: TaskStatus,
        *,
        requested_by: str,
        reason: str = "",
        expected_version: int | None = None,
        **overrides: object,
    ) -> tuple[Task, TaskStatus | None]:
        """Convenience: transition task status and return the updated Task.

        Args:
            task_id: Target task identifier.
            target_status: Desired target status.
            requested_by: Identity of the requester.
            reason: Reason for the transition.
            expected_version: Optional optimistic concurrency version.
            **overrides: Additional field overrides for the transition.

        Returns:
            Tuple of (transitioned task, status before the transition).
            The second element is ``None`` only when the underlying
            mutation does not provide previous status.

        Raises:
            TaskEngineNotRunningError: If the engine is not running.
            TaskEngineQueueFullError: If the queue is at capacity.
            TaskNotFoundError: If the task is not found.
            TaskVersionConflictError: If ``expected_version`` doesn't match.
            TaskMutationError: If the mutation fails.
        """
        effective_reason = reason or f"Transition to {target_status.value}"
        try:
            mutation = TransitionTaskMutation(
                request_id=uuid4().hex,
                requested_by=requested_by,
                task_id=task_id,
                target_status=target_status,
                reason=effective_reason,
                overrides=dict(overrides),
                expected_version=expected_version,
            )
        except PydanticValidationError as exc:
            raise TaskMutationError(str(exc)) from exc
        result = await self.submit(mutation)
        if not result.success:
            self._raise_typed_error(result)
        if result.task is None:
            msg = "Internal error: transition succeeded but task is None"
            raise TaskInternalError(msg)
        return result.task, result.previous_status

    async def delete_task(
        self,
        task_id: str,
        *,
        requested_by: str,
    ) -> bool:
        """Convenience: delete a task and return success.

        Args:
            task_id: Target task identifier.
            requested_by: Identity of the requester.

        Returns:
            ``True`` if the task was deleted.

        Raises:
            TaskEngineNotRunningError: If the engine is not running.
            TaskEngineQueueFullError: If the queue is at capacity.
            TaskNotFoundError: If the task is not found.
            TaskMutationError: If the mutation fails.
        """
        try:
            mutation = DeleteTaskMutation(
                request_id=uuid4().hex,
                requested_by=requested_by,
                task_id=task_id,
            )
        except PydanticValidationError as exc:
            raise TaskMutationError(str(exc)) from exc
        result = await self.submit(mutation)
        if not result.success:
            self._raise_typed_error(result)
        return True

    async def cancel_task(
        self,
        task_id: str,
        *,
        requested_by: str,
        reason: str,
    ) -> Task:
        """Convenience: cancel a task and return the cancelled Task.

        Args:
            task_id: Target task identifier.
            requested_by: Identity of the requester.
            reason: Reason for cancellation.

        Returns:
            The cancelled task.

        Raises:
            TaskEngineNotRunningError: If the engine is not running.
            TaskEngineQueueFullError: If the queue is at capacity.
            TaskNotFoundError: If the task is not found.
            TaskMutationError: If the mutation fails.
        """
        try:
            mutation = CancelTaskMutation(
                request_id=uuid4().hex,
                requested_by=requested_by,
                task_id=task_id,
                reason=reason,
            )
        except PydanticValidationError as exc:
            raise TaskMutationError(str(exc)) from exc
        result = await self.submit(mutation)
        if not result.success:
            self._raise_typed_error(result)
        if result.task is None:
            msg = "Internal error: cancel succeeded but task is None"
            raise TaskInternalError(msg)
        return result.task

    @staticmethod
    def _raise_typed_error(result: TaskMutationResult) -> Never:
        """Raise a typed error from a failed mutation result."""
        error = result.error or "Mutation failed"
        logger.warning(
            TASK_ENGINE_MUTATION_FAILED,
            request_id=result.request_id,
            error=error,
            error_code=result.error_code,
        )
        match result.error_code:
            case "not_found":
                raise TaskNotFoundError(error)
            case "version_conflict":
                raise TaskVersionConflictError(error)
            case "internal":
                raise TaskInternalError(error)
            case _:
                raise TaskMutationError(error)

    # -- Read-through (bypass queue) ---------------------------------------

    async def get_task(self, task_id: str) -> Task | None:
        """Read a task directly from persistence (bypass queue).

        Args:
            task_id: Task identifier.

        Returns:
            The task, or ``None`` if not found.

        Raises:
            TaskInternalError: If the persistence backend fails.
        """
        try:
            return await self._persistence.tasks.get(task_id)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            msg = f"Failed to read task: {exc}"
            logger.exception(
                TASK_ENGINE_READ_FAILED,
                error=msg,
                task_id=task_id,
            )
            raise TaskInternalError(msg) from exc

    async def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        assigned_to: str | None = None,
        project: str | None = None,
    ) -> tuple[tuple[Task, ...], int]:
        """List tasks directly from persistence (bypass queue).

        Returns a tuple of ``(tasks, total)`` where *total* is the true
        count before any safety cap is applied.  When the result set
        exceeds ``_MAX_LIST_RESULTS``, the returned tuple is truncated
        but *total* reflects the real cardinality so pagination metadata
        stays accurate.

        Args:
            status: Filter by status.
            assigned_to: Filter by assignee.
            project: Filter by project.

        Returns:
            ``(tasks, total)`` — *tasks* may be capped at
            ``_MAX_LIST_RESULTS``; *total* is the true count.

        Raises:
            TaskInternalError: If the persistence backend fails.
        """
        try:
            tasks = await self._persistence.tasks.list_tasks(
                status=status,
                assigned_to=assigned_to,
                project=project,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            msg = f"Failed to list tasks: {exc}"
            logger.exception(
                TASK_ENGINE_READ_FAILED,
                error=msg,
            )
            raise TaskInternalError(msg) from exc
        total = len(tasks)
        if total > self._MAX_LIST_RESULTS:
            logger.warning(
                TASK_ENGINE_LIST_CAPPED,
                actual_total=total,
                cap=self._MAX_LIST_RESULTS,
            )
            return tasks[: self._MAX_LIST_RESULTS], total
        return tasks, total

    # -- Background processing ---------------------------------------------

    _MAX_LIST_RESULTS: int = 10_000
    """Safety cap on ``list_tasks`` results to bound memory usage.

    Real pagination should be pushed into the persistence layer.
    """

    _POLL_INTERVAL_SECONDS: float = 0.5
    """How often the processing loop checks for ``_running = False``."""

    _SNAPSHOT_SENDER: str = "task-engine"
    """Sender identity used in snapshot ``Message`` envelopes."""

    _SNAPSHOT_CHANNEL: str = "tasks"
    """Message bus channel for snapshot publication.

    Must match ``CHANNEL_TASKS`` in ``api.channels`` so that events
    reach the MessageBusBridge and WebSocket consumers.
    """

    async def _processing_loop(self) -> None:
        """Background loop: dequeue and process mutations sequentially.

        Continues draining queued mutations after ``_running`` is set to
        ``False``, enabling graceful shutdown.
        """
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
            except (MemoryError, RecursionError) as exc:
                if not envelope.future.done():
                    envelope.future.set_exception(exc)
                raise
            except Exception:
                logger.exception(
                    TASK_ENGINE_LOOP_ERROR,
                    error="Unhandled exception in processing loop",
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
            if result.success and self._config.publish_snapshots:
                await self._publish_snapshot(mutation, result)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            internal_msg = f"{type(exc).__name__}: {exc}"
            logger.exception(
                TASK_ENGINE_MUTATION_FAILED,
                mutation_type=mutation.mutation_type,
                request_id=mutation.request_id,
                error=internal_msg,
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

    # -- Snapshot publishing -----------------------------------------------

    async def _publish_snapshot(
        self,
        mutation: TaskMutation,
        result: TaskMutationResult,
    ) -> None:
        """Publish a TaskStateChanged event to the message bus.

        Best-effort: failures are logged and swallowed (except
        ``MemoryError`` and ``RecursionError``, which propagate).
        """
        if self._message_bus is None:
            return

        if isinstance(mutation, DeleteTaskMutation):
            new_status = None
        elif result.task is not None:
            new_status = result.task.status
        else:
            new_status = None

        reason: str | None = getattr(mutation, "reason", None)
        task_id: str | None = getattr(mutation, "task_id", None)
        # For create mutations, task_id comes from the result
        if task_id is None and result.task is not None:
            task_id = result.task.id
        effective_task_id = task_id or "unknown"

        event = TaskStateChanged(
            mutation_type=mutation.mutation_type,
            request_id=mutation.request_id,
            requested_by=mutation.requested_by,
            task_id=effective_task_id,
            task=result.task,
            previous_status=result.previous_status,
            new_status=new_status,
            version=result.version,
            reason=reason,
            timestamp=datetime.now(UTC),
        )
        try:
            # Deferred to break circular import:
            # communication -> engine -> communication
            from synthorg.communication.enums import MessageType  # noqa: PLC0415
            from synthorg.communication.message import Message  # noqa: PLC0415

            msg = Message(
                timestamp=datetime.now(UTC),
                sender=self._SNAPSHOT_SENDER,
                to=self._SNAPSHOT_CHANNEL,
                type=MessageType.TASK_UPDATE,
                channel=self._SNAPSHOT_CHANNEL,
                content=event.model_dump_json(),
            )
            await self._message_bus.publish(msg)
            logger.debug(
                TASK_ENGINE_SNAPSHOT_PUBLISHED,
                mutation_type=mutation.mutation_type,
                request_id=mutation.request_id,
                task_id=task_id,
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                TASK_ENGINE_SNAPSHOT_PUBLISH_FAILED,
                mutation_type=mutation.mutation_type,
                request_id=mutation.request_id,
                task_id=task_id,
                exc_info=True,
            )
