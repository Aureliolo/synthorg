# module-kind: complex_service
"""Centralized single-writer task engine.

Owns all task state mutations via an ``asyncio.Queue``.  A single
background task processes mutations sequentially, persists results,
and publishes snapshots.  Reads bypass the queue (safe: single writer).
Observer notifications are dispatched via a separate background queue.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Never
from uuid import uuid4

from pydantic import ValidationError as PydanticValidationError

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.errors import (
    TaskEngineNotRunningError,
    TaskEngineQueueFullError,
    TaskInternalError,
    TaskMutationError,
    TaskNotFoundError,
    TaskVersionConflictError,
)
from synthorg.engine.task_engine_config import TaskEngineConfig
from synthorg.engine.task_engine_loops import (
    TaskEngineLoopsMixin,
    _MutationEnvelope,
)
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
from synthorg.engine.task_engine_version import TaskTimingTracker, VersionTracker
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.background_tasks import log_task_exceptions
from synthorg.observability.events.task_engine import (
    TASK_ENGINE_CREATED,
    TASK_ENGINE_DRAIN_TIMEOUT,
    TASK_ENGINE_LIST_CAPPED,
    TASK_ENGINE_LOOP_DIED,
    TASK_ENGINE_MUTATION_FAILED,
    TASK_ENGINE_NOT_RUNNING,
    TASK_ENGINE_OBSERVER_LOOP_DIED,
    TASK_ENGINE_QUEUE_FULL,
    TASK_ENGINE_READ_FAILED,
    TASK_ENGINE_START_REJECTED,
    TASK_ENGINE_STARTED,
    TASK_ENGINE_STOP_REJECTED,
    TASK_ENGINE_STOPPED,
)
from synthorg.observability.tracing.instrumentation import get_tracer

if TYPE_CHECKING:
    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)
_tracer = get_tracer(__name__)


class TaskEngine(TaskEngineLoopsMixin):
    """Centralized single-writer for all task state mutations.

    Actor-like pattern: mutations are queued, processed sequentially,
    persisted, and published.  Observer notifications are dispatched
    via a separate background queue so slow observers never block
    the mutation pipeline.

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
        # Eager init: ``submit`` may enqueue mutations before the
        # processing task is spawned; the queue must exist for the
        # atomic check-and-put in ``submit`` to work safely.
        # fmt: off
        self._queue: asyncio.Queue[_MutationEnvelope] = asyncio.Queue(maxsize=self._config.max_queue_size)  # lint-allow: loop-bound-init -- see.  # noqa: E501
        # fmt: on
        self._versions = VersionTracker()
        self._timings = TaskTimingTracker()
        self._processing_task: asyncio.Task[None] | None = None
        self._in_flight: _MutationEnvelope | None = None
        self._running = False
        # Eager init: stop() must be safe to call before start() has
        # ever run, and ``submit`` requires both locks present.
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see.
        # Hot-path admission lock: held only for the atomic check-
        # and-put in :meth:`submit`. ``stop()`` briefly acquires it
        # just long enough to publish ``_running = False`` so new
        # submits fast-fail immediately, then drains under
        # ``_lifecycle_lock`` only. Keeping this lock separate from
        # ``_lifecycle_lock`` is mandated by CLAUDE.md -- hot-path
        # traffic must not serialize against lifecycle transitions.
        # Eager init: ``submit`` is the hot-path entry and may fire
        # before any lifecycle method runs.
        self._admission_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see.
        self._observers: list[Callable[[TaskStateChanged], Awaitable[None]]] = []
        # Eager init: observer registration may happen before start()
        # so the queue must exist for ``register_observer`` to bind.
        # fmt: off
        self._observer_queue: asyncio.Queue[TaskStateChanged | None] = asyncio.Queue(maxsize=self._config.effective_observer_queue_size)  # lint-allow: loop-bound-init -- see.  # noqa: E501
        # fmt: on
        self._observer_task: asyncio.Task[None] | None = None
        # Set to True when a stop() drain exceeds the hard deadline.
        # Prevents a subsequent start() from creating a second loop
        # pair on top of the orphaned first generation. Clearing
        # requires reconstructing the engine -- there is no reset().
        self._unrestartable: bool = False
        logger.debug(
            TASK_ENGINE_CREATED,
            max_queue_size=self._config.max_queue_size,
            publish_snapshots=self._config.publish_snapshots,
        )

    # -- Observers ---------------------------------------------------------

    def register_observer(
        self,
        callback: Callable[[TaskStateChanged], Awaitable[None]],
    ) -> None:
        """Register a best-effort observer for successful task mutations.

        Args:
            callback: Async callable receiving the event.
        """
        self._observers.append(callback)

    # -- Lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Spawn the background processing loop.

        Holds ``_lifecycle_lock`` across the check-and-set +
        task-spawn so concurrent ``start()`` calls yield exactly one
        success, and a ``start()`` racing an in-flight ``stop()``
        cannot spawn a new processing task that escapes the stop's
        drain.

        Raises:
            RuntimeError: If already running.
        """
        async with self._lifecycle_lock:
            if self._unrestartable:
                # ``_unrestartable`` is set by ``stop()`` for BOTH the
                # hard-deadline (``TimeoutError``) path AND the caller
                # mid-drain cancellation (``CancelledError``) path.
                # Keep the message neutral so operators are not pointed
                # at the wrong failure mode.
                msg = (
                    "TaskEngine is unrestartable after a failed stop drain; "
                    "construct a fresh TaskEngine instead"
                )
                # Use the dedicated rejection event so a rejected
                # start does not inflate the successful-start metric.
                logger.warning(
                    TASK_ENGINE_START_REJECTED,
                    error=msg,
                    reason="unrestartable",
                )
                raise RuntimeError(msg)
            if self._running:
                msg = "TaskEngine is already running"
                logger.warning(
                    TASK_ENGINE_START_REJECTED,
                    error=msg,
                    reason="already_running",
                )
                raise RuntimeError(msg)
            # Hold ``_admission_lock`` across the entire startup so
            # a racing ``submit()`` cannot admit an envelope into the
            # queue between ``_running = True`` and the commit of both
            # loop tasks -- an envelope admitted in that window would
            # be orphaned if the rollback path fired, because both
            # loops would then be torn down with the future pending.
            # ``_running`` is published ONLY after both tasks are
            # created and their done-callbacks registered, so submit()
            # either sees False (and fast-fails cleanly) or sees True
            # with both loops committed.
            #
            # Transactional two-loop startup: if observer-task creation
            # or callback registration raises after the processing task
            # is up, cancel and drain any partially-created tasks,
            # reset the handles, leave ``_running = False``, and
            # re-raise so the caller observes a fully-rolled-back
            # state and can retry start() cleanly.
            async with self._admission_lock:
                try:
                    self._processing_task = asyncio.create_task(
                        self._processing_loop(),
                        name="task-engine-loop",
                    )
                    self._processing_task.add_done_callback(
                        log_task_exceptions(logger, TASK_ENGINE_LOOP_DIED),
                    )
                    self._observer_task = asyncio.create_task(
                        self._observer_dispatch_loop(),
                        name="task-engine-observer-dispatcher",
                    )
                    self._observer_task.add_done_callback(
                        log_task_exceptions(
                            logger,
                            TASK_ENGINE_OBSERVER_LOOP_DIED,
                        ),
                    )
                except BaseException:
                    partial_tasks = [
                        t
                        for t in (self._processing_task, self._observer_task)
                        if t is not None
                    ]
                    for t in partial_tasks:
                        t.cancel()
                    if partial_tasks:
                        # Best-effort drain; swallow exceptions so we
                        # do not mask the original failure we are
                        # about to re-raise. ``return_exceptions=True``
                        # collects CancelledError cleanly.
                        await asyncio.gather(
                            *partial_tasks,
                            return_exceptions=True,
                        )
                    self._processing_task = None
                    self._observer_task = None
                    # Log the rollback so operators see *why* the
                    # engine never came up -- without this the caller
                    # would receive the original exception but the
                    # structured breadcrumb for the partial-startup
                    # cleanup would be lost.
                    logger.error(
                        TASK_ENGINE_START_REJECTED,
                        reason="startup_rollback",
                        partial_tasks_cancelled=len(partial_tasks),
                    )
                    raise
                # Only publish ``_running = True`` after BOTH tasks are
                # fully committed. A racing submit() that reached the
                # admission lock before this point is now blocked on
                # it; once we release the lock submit() will read True
                # and proceed safely.
                self._running = True
            logger.info(
                TASK_ENGINE_STARTED,
                max_queue_size=self._config.max_queue_size,
            )

    async def stop(self, *, timeout: float | None = None) -> None:  # noqa: ASYNC109
        """Stop the engine and drain pending mutations and observer events.

        Holds ``_lifecycle_lock`` across the entire stop body
        (including the drain awaits) so a concurrent ``start()``
        cannot see ``_running=False`` mid-drain and spawn a new
        processing task that stop never waits on.

        Args:
            timeout: Seconds to wait for drain (default: config value).
                Must be positive when provided; ``None`` means "use the
                config default".

        Raises:
            ValueError: If ``timeout`` is not ``None`` and ``<= 0``.
            TimeoutError: If the drain exceeds the hard deadline
                (``2 * effective_timeout``); the engine is marked
                unrestartable.
            asyncio.CancelledError: If the caller cancels ``stop()``
                mid-drain; the engine is marked unrestartable so a
                later ``start()`` cannot attach a second loop pair on
                top of orphaned drain tasks.
        """
        # Validate at the system boundary so callers with a bad
        # argument never mutate lifecycle state. A zero / negative
        # timeout would otherwise immediately trip ``asyncio.wait_for``
        # and mark the engine ``_unrestartable`` even though nothing
        # actually hung -- the fresh instance rule exists for genuine
        # hung drains, not for malformed input.
        if timeout is not None and timeout <= 0:
            # Log before raising so malformed caller input reaches
            # task-engine telemetry rather than vanishing silently
            # (CLAUDE.md: "All error paths must log at WARNING or
            # ERROR with context before raising").
            logger.warning(
                TASK_ENGINE_STOP_REJECTED,
                note="stop() called with invalid timeout; raising ValueError",
                timeout=timeout,
                reason="invalid_timeout",
            )
            msg = f"stop() timeout must be > 0, got {timeout!r}"
            raise ValueError(msg)
        async with self._lifecycle_lock:
            if not self._running:
                return
            # Publish the shutdown flag under the admission lock so
            # racing ``submit()`` calls fast-fail with
            # ``TaskEngineNotRunningError`` instead of blocking on the
            # drain. The admission lock is only held for the atomic
            # flag flip -- the drain itself runs with only the
            # lifecycle lock, so hot-path callers don't pay for
            # shutdown latency.
            async with self._admission_lock:
                self._running = False
            effective_timeout = (
                timeout if timeout is not None else self._config.drain_timeout_seconds
            )
            # Outer hard deadline: even if individual drain stages
            # hang (e.g. a processing task ignores CancelledError or
            # is stuck in an uninterruptible sync block), the whole
            # stop sequence is bounded to ~2x the nominal drain
            # budget. Beyond that, we log CRITICAL and release the
            # lifecycle lock so subsequent start() calls do not block
            # forever -- the leaked tasks will be surfaced by the
            # done-callbacks registered in start().
            hard_deadline = effective_timeout * 2.0
            try:
                await asyncio.wait_for(
                    self._drain_all(effective_timeout),
                    timeout=hard_deadline,
                )
            except TimeoutError:
                # Mark the engine unrestartable so a subsequent start()
                # cannot attach a second loop pair on top of orphaned
                # processing / observer tasks that ignored cancellation.
                # Without this guard the single-writer invariant would
                # be silently broken: two generations of the loop pair
                # would concurrently pop from the same _queue and
                # dispatch to the same observers. Operator must
                # reconstruct a fresh TaskEngine to recover.
                self._unrestartable = True
                # TRY400: logger.exception here would append a
                # TimeoutError traceback with no actionable diagnostic
                # information beyond the structured fields below.
                # Use the dedicated drain-timeout event, NOT
                # TASK_ENGINE_STOPPED -- reserving the success event
                # for the clean-shutdown branch so failed drains are
                # classified correctly in metrics and alerts.
                logger.error(
                    TASK_ENGINE_DRAIN_TIMEOUT,
                    note=(
                        "stop exceeded hard deadline; "
                        "engine marked unrestartable (orphaned drain tasks)"
                    ),
                    hard_deadline_seconds=hard_deadline,
                )
                raise
            except asyncio.CancelledError:
                # Caller cancelled stop() while the drain was in
                # flight (e.g. the lifespan supervisor is itself being
                # cancelled). Mirror the TimeoutError branch: the
                # drain may still be running in the background with
                # orphaned processing / observer tasks attached to
                # ``_queue`` / ``_observer_queue``; allowing a later
                # ``start()`` would attach a second loop pair on top
                # of those and silently duplicate writes. Mark the
                # engine unrestartable, log, and re-raise the
                # cancellation so the caller's cancellation contract
                # is honoured.
                self._unrestartable = True
                # TRY400: attaching a CancelledError traceback here
                # adds no actionable context over the structured
                # fields below.
                logger.error(
                    TASK_ENGINE_DRAIN_TIMEOUT,
                    note=(
                        "stop cancelled mid-drain; "
                        "engine marked unrestartable (orphaned drain tasks)"
                    ),
                    hard_deadline_seconds=hard_deadline,
                    cancellation=True,
                )
                raise
            logger.info(TASK_ENGINE_STOPPED)

    async def _drain_all(self, effective_timeout: float) -> None:
        """Drain the mutation queue + observer queue within the given budget.

        Splits ``effective_timeout`` evenly between the processing-drain
        stage and the observer-drain stage so each stage is guaranteed
        at least ``effective_timeout / 2``. A slow processing
        cancellation (cancellation handshake latency under contention,
        for example) cannot starve the observer drain into a
        zero-budget call, and the outer
        ``hard_deadline = 2 * effective_timeout`` guard set by
        :meth:`stop` never fires on a normal drain. Wrapped in a single
        awaitable so the outer ``asyncio.wait_for`` has exactly one
        thing to bound.
        """
        stage_budget = effective_timeout / 2.0

        await self._drain_processing(stage_budget)
        # Signal the observer loop that no more events will arrive.
        # Bounded by the observer-stage budget -- if the queue is full
        # and the dispatcher is stuck, the suppressed TimeoutError lets
        # _drain_observer cancel the observer task on its own timeout.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                self._observer_queue.put(None),
                timeout=stage_budget,
            )
        await self._drain_observer(stage_budget)

    @property
    def is_running(self) -> bool:
        """Whether the engine is accepting mutations."""
        return self._running

    # -- Submit & convenience methods --------------------------------------

    async def submit(self, mutation: TaskMutation) -> TaskMutationResult:
        """Submit a mutation and await its result.

        Args:
            mutation: The mutation to apply.

        Returns:
            Result of the mutation.

        Raises:
            TaskEngineNotRunningError: If the engine is not running.
            TaskEngineQueueFullError: If the queue is at capacity.
        """
        with _tracer.start_as_current_span(
            "task_engine.mutation",
            attributes={
                "mutation.type": mutation.mutation_type,
                "mutation.request_id": mutation.request_id,
                "task.id": getattr(mutation, "task_id", "")
                or getattr(getattr(mutation, "task_data", None), "id", "")
                or "",
            },
        ) as span:
            # Use ``_admission_lock`` (hot-path) -- not
            # ``_lifecycle_lock`` -- so new submits are not serialized
            # against an in-flight ``stop()`` drain, which can hold
            # ``_lifecycle_lock`` for up to the hard-deadline budget.
            # ``stop()`` briefly takes ``_admission_lock`` to publish
            # ``_running=False``, so any racing submit either sees the
            # flag and fast-fails or wins the race and lands cleanly
            # in the queue before drain.
            async with self._admission_lock:
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

            result = await envelope.future
            span.set_attribute("mutation.success", result.success)
            if result.task is not None:
                span.set_attribute("task.status", result.task.status.value)
            return result

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
            TaskInternalError: If the mutation succeeds but the engine
                returns no task object (unexpected internal state).
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
            TaskInternalError: If the mutation succeeds but the engine
                returns no task object (unexpected internal state).
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
            TaskInternalError: If the mutation succeeds but the engine
                returns no task object (unexpected internal state).
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
    ) -> tuple[Task, TaskStatus | None]:
        """Convenience: cancel a task and return ``(task, previous_status)``.

        Args:
            task_id: Target task identifier.
            requested_by: Identity of the requester.
            reason: Reason for cancellation.

        Returns:
            Tuple of (cancelled task, status before cancellation).  The
            previous status is captured inside the actor lock so callers
            can audit the transition without a second ``get_task`` round
            trip that races concurrent mutation.  ``previous_status`` is
            ``None`` only when the underlying mutation didn't record one.

        Raises:
            TaskEngineNotRunningError: If the engine is not running.
            TaskEngineQueueFullError: If the queue is at capacity.
            TaskNotFoundError: If the task is not found.
            TaskMutationError: If the mutation fails.
            TaskInternalError: If the mutation succeeds but the engine
                returns no task object (unexpected internal state).
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
        return result.task, result.previous_status

    @staticmethod
    def _raise_typed_error(result: TaskMutationResult) -> Never:
        """Raise a typed error from a failed mutation result.

        Raises:
            TaskNotFoundError: When ``result.error_code`` is
                ``"not_found"``.
            TaskVersionConflictError: When ``result.error_code`` is
                ``"version_conflict"``.
            TaskInternalError: When ``result.error_code`` is
                ``"internal"``.
            TaskMutationError: For every other error code.
        """
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
        except Exception as exc:
            reraise_critical(exc)
            msg = f"Failed to read task: {safe_error_description(exc)}"
            logger.warning(
                TASK_ENGINE_READ_FAILED,
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise TaskInternalError(msg) from exc

    @staticmethod
    def _validate_pagination(limit: int | None, offset: int) -> None:
        """Reject negative or ill-composed pagination before touching the repo.

        ``offset > 0`` without a matching ``limit`` is rejected: the
        legacy fallback computes ``total = len(tasks)``, which would
        undercount the full cardinality once the offset has skipped
        leading rows.  Callers that want offset must pass an explicit
        ``limit`` so the engine can route through the paginated branch
        that issues a dedicated ``count_tasks`` round-trip.

        Raises:
            ValueError: If ``limit`` is negative, ``offset`` is
                negative, or ``offset > 0`` is passed without an
                explicit ``limit``.
        """
        if limit is not None and limit < 0:
            msg = f"limit must be non-negative when set; got {limit}"
            raise ValueError(msg)
        if offset < 0:
            msg = f"offset must be non-negative; got {offset}"
            raise ValueError(msg)
        if limit is None and offset > 0:
            msg = (
                f"offset ({offset}) requires an explicit limit; "
                "pass `limit` to use offset-based pagination"
            )
            raise ValueError(msg)

    async def _fetch_tasks(
        self,
        *,
        status: TaskStatus | None,
        assigned_to: str | None,
        project: str | None,
        limit: int | None,
        offset: int,
    ) -> tuple[Task, ...]:
        """Forward the filtered list to the repo with sanitised logging.

        ``limit=None`` means "fetch everything"; the repository protocol
        requires an ``int``, so translate it into the safety cap and
        rely on the in-memory truncation downstream.

        Returns:
            The tuple of matching tasks from the repository query.

        Raises:
            TaskInternalError: If the persistence backend fails.
        """
        from synthorg.persistence.task_protocol import TaskFilterSpec  # noqa: PLC0415

        repo_limit = self._MAX_LIST_RESULTS if limit is None else limit
        try:
            return await self._persistence.tasks.query(
                TaskFilterSpec(
                    status=status,
                    assigned_to=assigned_to,
                    project=project,
                ),
                limit=repo_limit,
                offset=offset,
            )
        except Exception as exc:
            reraise_critical(exc)
            msg = "Failed to list tasks"
            logger.warning(
                TASK_ENGINE_READ_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise TaskInternalError(msg) from exc

    async def _count_tasks_filtered(
        self,
        *,
        status: TaskStatus | None,
        assigned_to: str | None,
        project: str | None,
    ) -> int:
        """Accurate total count with sanitised logging.

        Returns:
            The number of tasks matching the filter spec.

        Raises:
            TaskInternalError: If the persistence backend fails.
        """
        from synthorg.persistence.task_protocol import TaskFilterSpec  # noqa: PLC0415

        try:
            return await self._persistence.tasks.count(
                TaskFilterSpec(
                    status=status,
                    assigned_to=assigned_to,
                    project=project,
                ),
            )
        except Exception as exc:
            reraise_critical(exc)
            msg = "Failed to count tasks"
            logger.warning(
                TASK_ENGINE_READ_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise TaskInternalError(msg) from exc

    async def list_tasks(  # noqa: PLR0913
        self,
        *,
        status: TaskStatus | None = None,
        assigned_to: str | None = None,
        project: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        include_total: bool = True,
    ) -> tuple[tuple[Task, ...], int | None]:
        """List tasks with push-down pagination.

        Callers that pass ``limit`` get the requested window straight
        out of the repository (no 10k safety truncation because the
        repo itself bounds the result).  Callers that pass ``limit=None``
        keep the legacy behaviour: fetch everything and apply the
        ``_MAX_LIST_RESULTS`` safety cap in-memory as defense-in-depth.

        Args:
            status: Filter by status.
            assigned_to: Filter by assignee.
            project: Filter by project.
            limit: Max rows to return; ``None`` retains the safety-capped
                "fetch all" semantics for legacy callers.
            offset: Rows to skip before the returned window.
            include_total: When ``True`` issue an additional ``count_tasks``
                call and return the true total; when ``False`` the
                second tuple element is ``None`` and the extra round
                trip is skipped (used by callers that only need
                ``has_more``).

        Returns:
            ``(tasks, total)`` where ``total`` is ``None`` iff
            ``include_total`` is ``False``.

        Raises:
            TaskInternalError: If the persistence backend fails.
            ValueError: If ``limit`` is negative, ``offset`` is negative,
                or ``offset > 0`` is passed without an explicit ``limit``
                (offset-based pagination requires a paired limit so the
                returned total stays accurate; see
                :meth:`_validate_pagination`).
        """
        self._validate_pagination(limit, offset)
        tasks = await self._fetch_tasks(
            status=status,
            assigned_to=assigned_to,
            project=project,
            limit=limit,
            offset=offset,
        )

        # When the caller paginates at the repo layer, ``tasks`` is
        # already bounded by the repo's safety cap. For the legacy
        # ``limit=None`` (fetch-all) path, ``_fetch_tasks`` pre-clamps
        # to ``_MAX_LIST_RESULTS`` at the repo layer so ``len(tasks)``
        # is the post-cap count, not the true total -- issuing an
        # extra ``count_tasks`` call here gives us the authoritative
        # pre-cap cardinality so ``TASK_ENGINE_LIST_CAPPED`` fires
        # AND the returned ``total`` reflects real cardinality even
        # when the tuple is truncated.  We also still apply the
        # in-memory truncation against the returned list so a
        # mis-mocked or non-clamping repo cannot bypass the safety
        # cap downstream; ``true_total`` then takes the maximum of
        # the count and the observed list length so a pathological
        # repo (e.g. test fixture returning more rows than count
        # reports) still surfaces the real pre-cap size.
        if limit is None:
            count_total = await self._count_tasks_filtered(
                status=status,
                assigned_to=assigned_to,
                project=project,
            )
            true_total = max(count_total, len(tasks))
            if true_total > self._MAX_LIST_RESULTS:
                logger.warning(
                    TASK_ENGINE_LIST_CAPPED,
                    actual_total=true_total,
                    cap=self._MAX_LIST_RESULTS,
                )
                tasks = tasks[: self._MAX_LIST_RESULTS]
        else:
            true_total = len(tasks)

        if not include_total:
            return tasks, None

        if limit is None:
            # Full-fetch path: ``true_total`` is the authoritative
            # pre-truncation count from ``count_tasks``.
            return tasks, true_total

        total = await self._count_tasks_filtered(
            status=status,
            assigned_to=assigned_to,
            project=project,
        )
        return tasks, total

    # -- Background processing ---------------------------------------------

    _MAX_LIST_RESULTS: int = 10_000
    """Defense-in-depth cap on unpaginated ``list_tasks`` calls.

    Applies only when ``limit is None``; paginated callers bypass the
    cap because the repository already bounds the result set.
    """

    _POLL_INTERVAL_SECONDS: float = 0.5
    """How often background loops check for shutdown."""

    _SNAPSHOT_SENDER: str = "task-engine"
    """Sender identity for snapshot ``Message`` envelopes."""

    _SNAPSHOT_CHANNEL: str = "tasks"
    """Snapshot channel (must match ``CHANNEL_TASKS`` in ``api.channels``)."""
