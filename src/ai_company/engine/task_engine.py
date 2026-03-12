"""Centralized single-writer task engine.

Owns all task state mutations via an ``asyncio.Queue``.  A single
background task consumes mutation requests sequentially, applies
``model_copy(update=...)`` on frozen ``Task`` models, persists the
result, and publishes snapshots to the message bus.

Reads bypass the queue and go directly to persistence — this is safe
because the TaskEngine is the only writer.
"""

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from ai_company.core.enums import TaskStatus
from ai_company.core.task import Task
from ai_company.engine.errors import (
    TaskEngineNotRunningError,
    TaskMutationError,
    TaskVersionConflictError,
)
from ai_company.engine.task_engine_config import TaskEngineConfig
from ai_company.engine.task_engine_models import (
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
from ai_company.observability import get_logger
from ai_company.observability.events.task_engine import (
    TASK_ENGINE_DRAIN_COMPLETE,
    TASK_ENGINE_DRAIN_START,
    TASK_ENGINE_DRAIN_TIMEOUT,
    TASK_ENGINE_MUTATION_APPLIED,
    TASK_ENGINE_MUTATION_FAILED,
    TASK_ENGINE_MUTATION_RECEIVED,
    TASK_ENGINE_NOT_RUNNING,
    TASK_ENGINE_QUEUE_FULL,
    TASK_ENGINE_SNAPSHOT_PUBLISH_FAILED,
    TASK_ENGINE_SNAPSHOT_PUBLISHED,
    TASK_ENGINE_STARTED,
    TASK_ENGINE_STOPPED,
)

if TYPE_CHECKING:
    from ai_company.communication.bus_protocol import MessageBus
    from ai_company.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)


@dataclass
class _MutationEnvelope:
    """Pairs a mutation request with its response future."""

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
        self._versions: dict[str, int] = {}
        self._processing_task: asyncio.Task[None] | None = None
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self) -> None:
        """Spawn the background processing loop.

        Raises:
            RuntimeError: If already running.
        """
        if self._running:
            msg = "TaskEngine is already running"
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

        Args:
            timeout: Seconds to wait for drain.  Defaults to
                ``config.drain_timeout_seconds``.
        """
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
                    self._processing_task,
                    timeout=effective_timeout,
                )
                logger.info(TASK_ENGINE_DRAIN_COMPLETE)
            except TimeoutError:
                logger.warning(
                    TASK_ENGINE_DRAIN_TIMEOUT,
                    remaining=self._queue.qsize(),
                )
                self._processing_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._processing_task
            self._processing_task = None

        logger.info(TASK_ENGINE_STOPPED)

    @property
    def is_running(self) -> bool:
        """Whether the engine is accepting mutations."""
        return self._running

    # ── Submit & convenience methods ──────────────────────────

    async def submit(self, mutation: TaskMutation) -> TaskMutationResult:
        """Submit a mutation and await its result.

        Args:
            mutation: The mutation to apply.

        Returns:
            Result of the mutation.

        Raises:
            TaskEngineNotRunningError: If the engine is not running.
        """
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
            raise TaskEngineNotRunningError(msg) from None

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
            TaskMutationError: If the mutation fails.
        """
        mutation = CreateTaskMutation(
            request_id=uuid4().hex,
            requested_by=requested_by,
            task_data=data,
        )
        result = await self.submit(mutation)
        if not result.success:
            raise TaskMutationError(result.error or "Create failed")
        assert result.task is not None  # noqa: S101
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
            TaskMutationError: If the mutation fails.
        """
        mutation = UpdateTaskMutation(
            request_id=uuid4().hex,
            requested_by=requested_by,
            task_id=task_id,
            updates=updates,
            expected_version=expected_version,
        )
        result = await self.submit(mutation)
        if not result.success:
            raise TaskMutationError(result.error or "Update failed")
        assert result.task is not None  # noqa: S101
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
    ) -> Task:
        """Convenience: transition task status and return the updated Task.

        Args:
            task_id: Target task identifier.
            target_status: Desired target status.
            requested_by: Identity of the requester.
            reason: Reason for the transition.
            expected_version: Optional optimistic concurrency version.
            **overrides: Additional field overrides for the transition.

        Returns:
            The transitioned task.

        Raises:
            TaskEngineNotRunningError: If the engine is not running.
            TaskMutationError: If the mutation fails.
        """
        effective_reason = reason or f"Transition to {target_status.value}"
        mutation = TransitionTaskMutation(
            request_id=uuid4().hex,
            requested_by=requested_by,
            task_id=task_id,
            target_status=target_status,
            reason=effective_reason,
            overrides=dict(overrides),
            expected_version=expected_version,
        )
        result = await self.submit(mutation)
        if not result.success:
            raise TaskMutationError(result.error or "Transition failed")
        assert result.task is not None  # noqa: S101
        return result.task

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
            TaskMutationError: If the mutation fails.
        """
        mutation = DeleteTaskMutation(
            request_id=uuid4().hex,
            requested_by=requested_by,
            task_id=task_id,
        )
        result = await self.submit(mutation)
        if not result.success:
            raise TaskMutationError(result.error or "Delete failed")
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
            TaskMutationError: If the mutation fails.
        """
        mutation = CancelTaskMutation(
            request_id=uuid4().hex,
            requested_by=requested_by,
            task_id=task_id,
            reason=reason,
        )
        result = await self.submit(mutation)
        if not result.success:
            raise TaskMutationError(result.error or "Cancel failed")
        assert result.task is not None  # noqa: S101
        return result.task

    # ── Read-through (bypass queue) ───────────────────────────

    async def get_task(self, task_id: str) -> Task | None:
        """Read a task directly from persistence (bypass queue).

        Args:
            task_id: Task identifier.

        Returns:
            The task, or ``None`` if not found.
        """
        return await self._persistence.tasks.get(task_id)

    async def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        assigned_to: str | None = None,
        project: str | None = None,
    ) -> tuple[Task, ...]:
        """List tasks directly from persistence (bypass queue).

        Args:
            status: Filter by status.
            assigned_to: Filter by assignee.
            project: Filter by project.

        Returns:
            Matching tasks as a tuple.
        """
        return await self._persistence.tasks.list_tasks(
            status=status,
            assigned_to=assigned_to,
            project=project,
        )

    # ── Background processing ─────────────────────────────────

    async def _processing_loop(self) -> None:
        """Background loop: dequeue and process mutations sequentially."""
        while self._running or not self._queue.empty():
            try:
                envelope = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=0.5,
                )
            except TimeoutError:
                continue
            await self._process_one(envelope)

    async def _process_one(self, envelope: _MutationEnvelope) -> None:
        """Process a single mutation envelope."""
        mutation = envelope.mutation
        logger.debug(
            TASK_ENGINE_MUTATION_RECEIVED,
            mutation_type=mutation.mutation_type,
            request_id=mutation.request_id,
        )
        try:
            result = await self._apply_mutation(mutation)
            envelope.future.set_result(result)
            if result.success and self._config.publish_snapshots:
                await self._publish_snapshot(mutation, result)
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.exception(
                TASK_ENGINE_MUTATION_FAILED,
                mutation_type=mutation.mutation_type,
                request_id=mutation.request_id,
                error=error_msg,
            )
            if not envelope.future.done():
                envelope.future.set_result(
                    TaskMutationResult(
                        request_id=mutation.request_id,
                        success=False,
                        error=error_msg,
                    ),
                )

    async def _apply_mutation(self, mutation: TaskMutation) -> TaskMutationResult:
        """Dispatch and apply a mutation by type."""
        match mutation:
            case CreateTaskMutation():
                return await self._apply_create(mutation)
            case UpdateTaskMutation():
                return await self._apply_update(mutation)
            case TransitionTaskMutation():
                return await self._apply_transition(mutation)
            case DeleteTaskMutation():
                return await self._apply_delete(mutation)
            case CancelTaskMutation():
                return await self._apply_cancel(mutation)

    async def _apply_create(
        self,
        mutation: CreateTaskMutation,
    ) -> TaskMutationResult:
        """Create a new task."""
        data = mutation.task_data
        task_id = f"task-{uuid4().hex}"

        task = Task(
            id=task_id,
            title=data.title,
            description=data.description,
            type=data.type,
            priority=data.priority,
            project=data.project,
            created_by=data.created_by,
            assigned_to=data.assigned_to,
            estimated_complexity=data.estimated_complexity,
            budget_limit=data.budget_limit,
        )
        await self._persistence.tasks.save(task)
        self._versions[task_id] = 1

        logger.info(
            TASK_ENGINE_MUTATION_APPLIED,
            mutation_type="create",
            request_id=mutation.request_id,
            task_id=task_id,
        )
        return TaskMutationResult(
            request_id=mutation.request_id,
            success=True,
            task=task,
            version=1,
        )

    async def _apply_update(
        self,
        mutation: UpdateTaskMutation,
    ) -> TaskMutationResult:
        """Update task fields."""
        task = await self._persistence.tasks.get(mutation.task_id)
        if task is None:
            return TaskMutationResult(
                request_id=mutation.request_id,
                success=False,
                error=f"Task {mutation.task_id!r} not found",
            )

        self._check_version(mutation.task_id, mutation.expected_version)

        if not mutation.updates:
            version = self._versions.get(mutation.task_id, 0)
            return TaskMutationResult(
                request_id=mutation.request_id,
                success=True,
                task=task,
                version=version,
            )

        updated = task.model_copy(update=mutation.updates)
        await self._persistence.tasks.save(updated)
        version = self._bump_version(mutation.task_id)

        logger.info(
            TASK_ENGINE_MUTATION_APPLIED,
            mutation_type="update",
            request_id=mutation.request_id,
            task_id=mutation.task_id,
            fields=list(mutation.updates),
        )
        return TaskMutationResult(
            request_id=mutation.request_id,
            success=True,
            task=updated,
            version=version,
        )

    async def _apply_transition(
        self,
        mutation: TransitionTaskMutation,
    ) -> TaskMutationResult:
        """Perform a task status transition."""
        task = await self._persistence.tasks.get(mutation.task_id)
        if task is None:
            return TaskMutationResult(
                request_id=mutation.request_id,
                success=False,
                error=f"Task {mutation.task_id!r} not found",
            )

        self._check_version(mutation.task_id, mutation.expected_version)
        previous_status = task.status

        try:
            updated = task.with_transition(
                mutation.target_status,
                **mutation.overrides,
            )
        except ValueError as exc:
            return TaskMutationResult(
                request_id=mutation.request_id,
                success=False,
                error=str(exc),
            )

        await self._persistence.tasks.save(updated)
        version = self._bump_version(mutation.task_id)

        logger.info(
            TASK_ENGINE_MUTATION_APPLIED,
            mutation_type="transition",
            request_id=mutation.request_id,
            task_id=mutation.task_id,
            from_status=previous_status.value,
            to_status=mutation.target_status.value,
        )
        return TaskMutationResult(
            request_id=mutation.request_id,
            success=True,
            task=updated,
            version=version,
        )

    async def _apply_delete(
        self,
        mutation: DeleteTaskMutation,
    ) -> TaskMutationResult:
        """Delete a task."""
        deleted = await self._persistence.tasks.delete(mutation.task_id)
        if not deleted:
            return TaskMutationResult(
                request_id=mutation.request_id,
                success=False,
                error=f"Task {mutation.task_id!r} not found",
            )

        self._versions.pop(mutation.task_id, None)

        logger.info(
            TASK_ENGINE_MUTATION_APPLIED,
            mutation_type="delete",
            request_id=mutation.request_id,
            task_id=mutation.task_id,
        )
        return TaskMutationResult(
            request_id=mutation.request_id,
            success=True,
            version=0,
        )

    async def _apply_cancel(
        self,
        mutation: CancelTaskMutation,
    ) -> TaskMutationResult:
        """Cancel a task (shortcut for transition to CANCELLED)."""
        task = await self._persistence.tasks.get(mutation.task_id)
        if task is None:
            return TaskMutationResult(
                request_id=mutation.request_id,
                success=False,
                error=f"Task {mutation.task_id!r} not found",
            )

        previous_status = task.status
        try:
            updated = task.with_transition(TaskStatus.CANCELLED)
        except ValueError as exc:
            return TaskMutationResult(
                request_id=mutation.request_id,
                success=False,
                error=str(exc),
            )

        await self._persistence.tasks.save(updated)
        version = self._bump_version(mutation.task_id)

        logger.info(
            TASK_ENGINE_MUTATION_APPLIED,
            mutation_type="cancel",
            request_id=mutation.request_id,
            task_id=mutation.task_id,
            from_status=previous_status.value,
            to_status=TaskStatus.CANCELLED.value,
        )
        return TaskMutationResult(
            request_id=mutation.request_id,
            success=True,
            task=updated,
            version=version,
        )

    # ── Snapshot publishing ───────────────────────────────────

    async def _publish_snapshot(
        self,
        mutation: TaskMutation,
        result: TaskMutationResult,
    ) -> None:
        """Publish a TaskStateChanged event to the message bus.

        Best-effort: failures are logged and swallowed.
        """
        if self._message_bus is None:
            return

        new_status = (
            None
            if isinstance(mutation, DeleteTaskMutation)
            else (result.task.status if result.task else None)
        )

        event = TaskStateChanged(
            mutation_type=mutation.mutation_type,
            request_id=mutation.request_id,
            requested_by=mutation.requested_by,
            task=result.task,
            previous_status=None,
            new_status=new_status,
            version=result.version,
            timestamp=datetime.now(UTC),
        )

        try:
            from ai_company.communication.enums import MessageType  # noqa: PLC0415
            from ai_company.communication.message import Message  # noqa: PLC0415

            msg = Message(
                timestamp=datetime.now(UTC),
                sender="task-engine",
                to="task_engine",
                type=MessageType.TASK_UPDATE,
                channel="task_engine",
                content=event.model_dump_json(),
            )
            await self._message_bus.publish(msg)
            logger.debug(
                TASK_ENGINE_SNAPSHOT_PUBLISHED,
                mutation_type=mutation.mutation_type,
                request_id=mutation.request_id,
            )
        except Exception:
            logger.warning(
                TASK_ENGINE_SNAPSHOT_PUBLISH_FAILED,
                mutation_type=mutation.mutation_type,
                request_id=mutation.request_id,
                exc_info=True,
            )

    # ── Version tracking ──────────────────────────────────────

    def _bump_version(self, task_id: str) -> int:
        """Increment and return the version counter for a task."""
        version = self._versions.get(task_id, 0) + 1
        self._versions[task_id] = version
        return version

    def _check_version(
        self,
        task_id: str,
        expected_version: int | None,
    ) -> None:
        """Check optimistic concurrency version if provided.

        Raises:
            TaskVersionConflictError: If versions don't match.
        """
        if expected_version is None:
            return
        current = self._versions.get(task_id, 0)
        if current != expected_version:
            msg = (
                f"Version conflict for task {task_id!r}: "
                f"expected {expected_version}, current {current}"
            )
            raise TaskVersionConflictError(msg)
