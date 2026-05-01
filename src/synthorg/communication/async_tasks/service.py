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
from synthorg.communication.bus_protocol import MessageBus  # noqa: TC001
from synthorg.communication.enums import MessagePriority, MessageType
from synthorg.communication.message import Message, TextPart
from synthorg.core.enums import TaskStatus, TaskType
from synthorg.engine.task_engine import TaskEngine  # noqa: TC001
from synthorg.engine.task_engine_models import CreateTaskData
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.async_task import (
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
                task.id,
                TaskStatus.ASSIGNED,
                requested_by=supervisor_id,
                reason="async_task_start",
                assigned_to=task_spec.agent_id,
                parent_task_id=task_spec.parent_task_id,
            )
        except MemoryError, RecursionError:
            # Process-fatal builtins propagate before any logging /
            # rollback work runs -- project convention for system-error
            # propagation.
            raise
        except Exception as exc:
            # Include ``task_id`` when the create succeeded so this
            # primary failure log can be correlated with the rollback
            # warning below (which already carries it).  ``None`` when
            # ``create_task`` raised before any row was created.
            logger.warning(
                ASYNC_TASK_START_FAILED,
                supervisor_id=supervisor_id,
                title=task_spec.title,
                task_id=task.id if task is not None else None,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            if task is not None:
                try:
                    await self._engine.cancel_task(
                        task.id,
                        requested_by=supervisor_id,
                        reason="assignment_failed",
                    )
                except Exception as cancel_exc:
                    logger.warning(
                        ASYNC_TASK_START_FAILED,
                        task_id=task.id,
                        error_type=type(cancel_exc).__name__,
                        reason="rollback cancel also failed",
                        error=safe_error_description(cancel_exc),
                    )
            raise

        logger.info(
            ASYNC_TASK_STARTED,
            task_id=task.id,
            agent_id=task_spec.agent_id,
            supervisor_id=supervisor_id,
        )
        # ``transition_task`` lands the row in ``TaskStatus.ASSIGNED``,
        # which maps to ``AsyncTaskStatus.PENDING`` (see
        # ``_STATUS_MAP``).  Logging ``RUNNING`` here would record a
        # status the database never actually held.  Use the mapped
        # value of the persisted status so the audit stream stays
        # consistent with the row-level state machine.
        persisted_status = self._map_status(task.status)
        logger.info(
            ASYNC_TASK_STATUS_TRANSITIONED,
            task_id=task.id,
            from_status=None,
            to_status=persisted_status.value,
        )
        return task.id

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
        # ``get_task`` reads persistence directly and bypasses the
        # mutation queue, while ``cancel_task`` goes through the
        # sequential actor; under concurrent mutation the
        # ``prior_status`` captured here may not reflect the value
        # that ``cancel_task`` actually transitioned from.  The
        # downstream ``ASYNC_TASK_STATUS_TRANSITIONED`` event therefore
        # carries best-effort audit data, not a strict happens-before
        # guarantee.  The right durable fix is to expose a
        # ``cancel_task`` variant on ``TaskEngine`` that returns both
        # ``old_status`` and ``new_status`` from inside the actor lock;
        # tracked separately from this controller change so the engine
        # API churn is atomic.
        prior_task = await self._engine.get_task(task_id)
        prior_status = (
            self._map_status(prior_task.status) if prior_task is not None else None
        )
        task = await self._engine.cancel_task(
            task_id,
            requested_by=supervisor_id,
            reason="ASYNC_CANCEL",
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
        """Map TaskEngine status to AsyncTaskStatus with warning on unknown."""
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
            (t.id, self._map_status(t.status))
            for t in tasks
            if t.parent_task_id == supervisor_task_id
        )
        logger.debug(
            ASYNC_TASK_LISTED,
            supervisor_task_id=supervisor_task_id,
            count=len(children),
        )
        return children
