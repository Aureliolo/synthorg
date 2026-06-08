"""Async task service -- supervisor-facing interface over TaskEngine.

Provides five steering operations (start, check, update, cancel, list)
that wrap the existing ``TaskEngine`` single-writer actor. Does NOT
create a parallel task system.
"""

from datetime import UTC, datetime

from synthorg.communication.async_tasks.models import (
    AsyncTaskStatus,
    TaskSpec,
)
from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.enums import MessagePriority, MessageType
from synthorg.communication.message import Message, TextPart
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import CreateTaskData
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.async_task import (
    ASYNC_TASK_CANCEL_FAILED,
    ASYNC_TASK_CANCELLED,
    ASYNC_TASK_CHECKED,
    ASYNC_TASK_LISTED,
    ASYNC_TASK_START_FAILED,
    ASYNC_TASK_STARTED,
    ASYNC_TASK_STATUS_TRANSITIONED,
    ASYNC_TASK_UPDATED,
)

logger = get_logger(__name__)

# Map internal TaskStatus to supervisor-facing AsyncTaskStatus.
_TERMINAL_STATUSES: frozenset[TaskStatus] = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.REJECTED,
        TaskStatus.INTERRUPTED,
    }
)

_STATUS_MAP: dict[TaskStatus, AsyncTaskStatus] = {
    TaskStatus.CREATED: AsyncTaskStatus.PENDING,
    TaskStatus.ASSIGNED: AsyncTaskStatus.PENDING,
    TaskStatus.IN_PROGRESS: AsyncTaskStatus.RUNNING,
    TaskStatus.IN_REVIEW: AsyncTaskStatus.RUNNING,
    TaskStatus.BLOCKED: AsyncTaskStatus.RUNNING,
    TaskStatus.AUTH_REQUIRED: AsyncTaskStatus.PENDING,
    TaskStatus.COMPLETED: AsyncTaskStatus.COMPLETED,
    TaskStatus.FAILED: AsyncTaskStatus.FAILED,
    TaskStatus.CANCELLED: AsyncTaskStatus.CANCELLED,
    TaskStatus.REJECTED: AsyncTaskStatus.FAILED,
    TaskStatus.INTERRUPTED: AsyncTaskStatus.FAILED,
    TaskStatus.SUSPENDED: AsyncTaskStatus.PENDING,
}


class AsyncTaskService:
    """Thin supervisor-facing interface over TaskEngine.

    All five operations delegate to the existing ``TaskEngine``
    single-writer actor. No parallel task system is created.

    Args:
        task_engine: The existing task engine instance.
        message_bus: Message bus for context injection messages.
    """

    __slots__ = ("_bus", "_engine")

    def __init__(
        self,
        *,
        task_engine: TaskEngine,
        message_bus: MessageBus,
    ) -> None:
        self._engine = task_engine
        self._bus = message_bus

    async def start_async_task(
        self,
        supervisor_id: str,
        task_spec: TaskSpec,
    ) -> str:
        """Create and assign a task via TaskEngine, return task ID.

        Args:
            supervisor_id: ID of the supervisor starting the task.
            task_spec: Specification of what the subagent should do.

        Returns:
            The created task's ID.
        """
        data = CreateTaskData(
            title=task_spec.title,
            description=task_spec.description,
            type=TaskType.RESEARCH,
            project="default",
            created_by=supervisor_id,
        )
        task = None
        try:
            task = await self._engine.create_task(
                data,
                requested_by=supervisor_id,
            )
            # ``transition_task`` returns ``tuple[Task, TaskStatus | None]``;
            # capture the post-transition Task so subsequent logs read
            # the actual persisted status (TaskStatus.ASSIGNED) instead
            # of the stale create-time status (TaskStatus.CREATED).
            task, _prior_status = await self._engine.transition_task(
                str(task.id),
                TaskStatus.ASSIGNED,
                requested_by=supervisor_id,
                reason="async_task_start",
                assigned_to=task_spec.agent_id,
                parent_task_id=task_spec.parent_task_id,
            )
        except Exception as exc:
            reraise_critical(exc)
            # Include ``task_id`` when the create succeeded so this
            # primary failure log can be correlated with the rollback
            # warning below (which already carries it).  ``None`` when
            # ``create_task`` raised before any row was created.
            logger.warning(
                ASYNC_TASK_START_FAILED,
                supervisor_id=supervisor_id,
                title=task_spec.title,
                task_id=str(task.id) if task is not None else None,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            if task is not None:
                await self._rollback_after_failed_start(task, supervisor_id)
            raise

        self._emit_start_audit(task, task_spec, supervisor_id)
        return str(task.id)

    async def _rollback_after_failed_start(
        self,
        task: Task,
        supervisor_id: str,
    ) -> None:
        """Compensating rollback after a failed start_async_task.

        Re-queries persisted state (``transition_task`` may have
        committed before raising), then ``delete_task`` for the
        still-CREATED row or ``cancel_task`` for any later state.
        ``CREATED -> CANCELLED`` is rejected by the task-state
        machine, so branching on the in-memory ``task.status`` would
        orphan the row.  Cancellation uses ``cancel_task``'s
        lock-captured previous-status return for the transition
        audit (a stale ``get_task`` snapshot would drift).
        """
        rollback_action = "unknown"
        try:
            persisted_task = await self._engine.get_task(str(task.id))
            rollback_status = (
                persisted_task.status if persisted_task is not None else None
            )
            if rollback_status is TaskStatus.CREATED:
                rollback_action = "delete"
                await self._engine.delete_task(
                    str(task.id),
                    requested_by=supervisor_id,
                )
            elif rollback_status is not None:
                rollback_action = "cancel"
                cancelled_task, rollback_prior = await self._engine.cancel_task(
                    str(task.id),
                    requested_by=supervisor_id,
                    reason="assignment_failed",
                )
                self._emit_rollback_cancel_audit(
                    task,
                    supervisor_id,
                    cancelled_task,
                    rollback_prior,
                )
        except Exception as cancel_exc:
            reraise_critical(cancel_exc)
            logger.warning(
                ASYNC_TASK_START_FAILED,
                task_id=str(task.id),
                error_type=type(cancel_exc).__name__,
                reason=f"rollback {rollback_action} also failed",
                error=safe_error_description(cancel_exc),
            )

    def _emit_rollback_cancel_audit(
        self,
        task: Task,
        supervisor_id: str,
        cancelled_task: Task,
        rollback_prior: TaskStatus | None,
    ) -> None:
        """Emit the rollback-path cancel + transition logs.

        Mirrors ``cancel_async_task``'s post-persist logs so the
        supervisor-facing audit stream records the compensating
        transition.
        """
        rollback_to_status = self._map_status(cancelled_task.status)
        rollback_from_status = (
            self._map_status(rollback_prior) if rollback_prior is not None else None
        )
        logger.info(
            ASYNC_TASK_CANCELLED,
            task_id=str(task.id),
            supervisor_id=supervisor_id,
            reason="assignment_failed",
        )
        if rollback_from_status != rollback_to_status:
            logger.info(
                ASYNC_TASK_STATUS_TRANSITIONED,
                task_id=str(task.id),
                from_status=(
                    rollback_from_status.value
                    if rollback_from_status is not None
                    else None
                ),
                to_status=rollback_to_status.value,
            )

    def _emit_start_audit(
        self,
        task: Task,
        task_spec: TaskSpec,
        supervisor_id: str,
    ) -> None:
        """Emit ASYNC_TASK_STARTED + ASYNC_TASK_STATUS_TRANSITIONED.

        ``transition_task`` lands the row in ``TaskStatus.ASSIGNED``,
        which maps to ``AsyncTaskStatus.PENDING`` (see
        ``_STATUS_MAP``).  Logging ``RUNNING`` here would record a
        status the database never actually held.  Use the mapped
        value of the persisted status so the audit stream stays
        consistent with the row-level state machine.
        """
        logger.info(
            ASYNC_TASK_STARTED,
            task_id=str(task.id),
            agent_id=task_spec.agent_id,
            supervisor_id=supervisor_id,
        )
        persisted_status = self._map_status(task.status)
        logger.info(
            ASYNC_TASK_STATUS_TRANSITIONED,
            task_id=str(task.id),
            from_status=None,
            to_status=persisted_status.value,
        )

    async def check_async_task(self, task_id: str) -> AsyncTaskStatus:
        """Project TaskEngine state to AsyncTaskStatus.

        Args:
            task_id: Task identifier to check.

        Returns:
            Current status from the supervisor's perspective.

        Raises:
            LookupError: If the task is not found.
        """
        task = await self._engine.get_task(task_id)
        if task is None:
            msg = f"Async task {task_id} not found"
            raise LookupError(msg)

        status = self._map_status(task.status)
        logger.debug(
            ASYNC_TASK_CHECKED,
            task_id=task_id,
            status=status.value,
        )
        return status

    async def update_async_task(
        self,
        task_id: str,
        instructions: str,
    ) -> AsyncTaskStatus:
        """Send new instructions to a running task via MessageBus.

        Posts a ``CONTEXT_INJECTION`` message to the executing agent.

        Args:
            task_id: Task to update.
            instructions: New instructions for the executing agent.

        Returns:
            Current status of the task.

        Raises:
            LookupError: If the task is not found.
        """
        task = await self._engine.get_task(task_id)
        if task is None:
            msg = f"Async task {task_id} not found"
            raise LookupError(msg)

        if task.status in _TERMINAL_STATUSES:
            msg = (
                f"Cannot update async task {task_id}: "
                f"task is in terminal state {task.status.value}"
            )
            logger.warning(
                ASYNC_TASK_UPDATED,
                task_id=task_id,
                error=msg,
            )
            raise LookupError(msg)

        recipient = task.assigned_to
        if recipient is None:
            msg = (
                f"Cannot update async task {task_id}: "
                f"no assigned agent (task still in {task.status.value} state)"
            )
            logger.warning(
                ASYNC_TASK_UPDATED,
                task_id=task_id,
                error=msg,
            )
            raise LookupError(msg)
        message = Message(
            timestamp=datetime.now(UTC),
            sender="async_task_service",
            to=recipient,
            type=MessageType.CONTEXT_INJECTION,
            priority=MessagePriority.NORMAL,
            channel=f"@async_task:{task_id}",
            parts=(TextPart(text=instructions),),
        )
        await self._bus.send_direct(message, recipient=recipient)

        status = self._map_status(task.status)
        logger.info(
            ASYNC_TASK_UPDATED,
            task_id=task_id,
            recipient=recipient,
        )
        return status

    async def cancel_async_task(
        self,
        task_id: str,
        supervisor_id: str,
    ) -> AsyncTaskStatus:
        """Cancel a task via TaskEngine.

        Args:
            task_id: Task to cancel.
            supervisor_id: ID of the supervisor requesting cancellation.

        Returns:
            Updated status (should be CANCELLED).
        """
        # ``cancel_task`` returns the previous status captured INSIDE
        # the actor lock, so the transition log below carries
        # happens-before-correct audit data even under concurrent
        # mutation -- no second ``get_task`` round trip needed.
        try:
            task, prior_task_status = await self._engine.cancel_task(
                task_id,
                requested_by=supervisor_id,
                reason="ASYNC_CANCEL",
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                ASYNC_TASK_CANCEL_FAILED,
                task_id=task_id,
                supervisor_id=supervisor_id,
                reason="ASYNC_CANCEL",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        prior_status = (
            self._map_status(prior_task_status)
            if prior_task_status is not None
            else None
        )
        status = self._map_status(task.status)
        logger.info(
            ASYNC_TASK_CANCELLED,
            task_id=task_id,
            supervisor_id=supervisor_id,
        )
        if prior_status != status:
            logger.info(
                ASYNC_TASK_STATUS_TRANSITIONED,
                task_id=task_id,
                from_status=prior_status.value if prior_status is not None else None,
                to_status=status.value,
            )
        return status

    def _map_status(self, task_status: TaskStatus) -> AsyncTaskStatus:
        """Map TaskEngine status to AsyncTaskStatus with warning on unknown.

        Returns:
            The mapped ``AsyncTaskStatus``; a safe fallback (logged) for
            unknown engine statuses.
        """
        result = _STATUS_MAP.get(task_status)
        if result is None:
            logger.warning(
                ASYNC_TASK_CHECKED,
                unknown_status=task_status.value,
                fallback="pending",
            )
            return AsyncTaskStatus.PENDING
        return result

    async def list_async_tasks(
        self,
        supervisor_task_id: str,
    ) -> tuple[tuple[str, AsyncTaskStatus], ...]:
        """List task ID + status pairs under a supervisor task.

        Filters TaskEngine tasks by ``parent_task_id``.

        Args:
            supervisor_task_id: The supervisor's own task ID.

        Returns:
            Tuple of ``(task_id, status)`` pairs for all child tasks.
        """
        # TaskEngine.list_tasks doesn't filter by parent_task_id
        # directly, so we fetch and filter in-memory.
        tasks, _count = await self._engine.list_tasks()
        children = tuple(
            (str(t.id), self._map_status(t.status))
            for t in tasks
            if t.parent_task_id == supervisor_task_id
        )
        logger.debug(
            ASYNC_TASK_LISTED,
            supervisor_task_id=supervisor_task_id,
            count=len(children),
        )
        return children
