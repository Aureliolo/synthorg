# module-kind: service
"""Lifecycle-only worker execution baseline (no LLM)."""

from typing import TYPE_CHECKING

from synthorg.core.domain_errors import (
    AgentRuntimeNotConfiguredError,
    NotFoundError,
)
from synthorg.core.enums import TaskStatus
from synthorg.core.task import (
    Task,
)
from synthorg.observability import (
    get_logger,
)
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_RESUME_FAILED,
)
from synthorg.observability.events.workers import (
    WORKERS_EXECUTION_SERVICE_ATTEMPTED,
    WORKERS_EXECUTION_SERVICE_COMPLETED,
    WORKERS_EXECUTION_SERVICE_NO_OP,
    WORKERS_EXECUTION_SERVICE_TASK_NOT_FOUND,
)

if TYPE_CHECKING:
    from synthorg.engine.task_engine import TaskEngine

logger = get_logger(__name__)


class LifecycleAdvancingExecutionService:
    """Lifecycle-only :class:`WorkerExecutionService` baseline.

    Advances the task one transition forward when it is in an
    executable state (``ASSIGNED`` or ``IN_PROGRESS``); returns the
    current state unchanged otherwise. No LLM, no tools: a claim
    arrives, the service rolls the lifecycle forward, the worker sees a
    terminal-or-not response and acks accordingly.

    This is what the dispatcher + queue + worker integration tests pin,
    and the fallback the ``AppState.worker_execution_service`` property
    lazily self-constructs when no explicit service is installed. The
    real agent runtime is a sibling class in this module,
    :class:`AgentEngineExecutionService`, selected by the runtime
    builder behind the provider-present switch.
    """

    __slots__ = ("_task_engine",)

    def __init__(self, *, task_engine: TaskEngine) -> None:
        self._task_engine = task_engine

    async def execute_once(
        self,
        *,
        task_id: str,
        previous_status: str | None,
        new_status: str,
        idempotency_key: str,
        requested_by: str,
    ) -> Task:
        """Walk the task one step forward through the lifecycle.

        ``ASSIGNED`` -> ``IN_PROGRESS`` -> ``IN_REVIEW`` -> ``COMPLETED``
        is the canonical happy path. Tasks in any other status are
        returned unchanged; the worker maps that into a retry so the
        next dispatch picks up the new state.

        Returns:
            The task after one lifecycle step (unchanged when it is
            outside the executable window).

        Raises:
            NotFoundError: When no task has ``task_id``.
        """
        task = await self._task_engine.get_task(task_id)
        if task is None:
            logger.warning(
                WORKERS_EXECUTION_SERVICE_TASK_NOT_FOUND,
                task_id=task_id,
                reason="task_not_found",
                previous_status=previous_status,
                new_status=new_status,
                idempotency_key=idempotency_key,
            )
            msg = f"Task {task_id!r} not found"
            raise NotFoundError(msg)
        current_status = task.status
        logger.info(
            WORKERS_EXECUTION_SERVICE_ATTEMPTED,
            task_id=task_id,
            current_status=current_status.value,
            previous_status=previous_status,
            new_status=new_status,
            idempotency_key=idempotency_key,
        )
        target = self._next_status(current_status)
        if target is None:
            logger.info(
                WORKERS_EXECUTION_SERVICE_NO_OP,
                task_id=task_id,
                current_status=current_status.value,
                reason="not_in_executable_status",
            )
            return task
        advanced, _ = await self._task_engine.transition_task(
            task_id,
            target,
            requested_by=requested_by,
            reason=f"worker execution step from {current_status.value}",
        )
        logger.info(
            WORKERS_EXECUTION_SERVICE_COMPLETED,
            task_id=task_id,
            from_status=task.status.value,
            to_status=advanced.status.value,
        )
        return advanced

    @staticmethod
    def _next_status(current: TaskStatus) -> TaskStatus | None:
        """Return the next baseline transition target for the lifecycle.

        Returns ``None`` for statuses outside the executable window;
        the worker maps that into a retry so any subsequent dispatch
        picks up the new state.
        """
        if current == TaskStatus.ASSIGNED:
            return TaskStatus.IN_PROGRESS
        if current == TaskStatus.IN_PROGRESS:
            return TaskStatus.IN_REVIEW
        if current == TaskStatus.IN_REVIEW:
            return TaskStatus.COMPLETED
        return None

    async def dispatch_resume(
        self,
        *,
        approval_id: str,
        approved: bool,
        decided_by: str,
        decision_reason: str | None,
    ) -> None:
        """Reject: the lifecycle baseline has no agent engine to resume.

        A parked context only exists when a real ``AgentEngine`` ran
        and parked, so reaching this baseline with one is a
        misconfiguration (the runtime service was never installed).
        Fail loudly rather than silently dropping the resume.

        Raises:
            AgentRuntimeNotConfiguredError: Always; the lifecycle
                baseline has no agent engine to resume into.
        """
        logger.error(
            APPROVAL_GATE_RESUME_FAILED,
            approval_id=approval_id,
            approved=approved,
            decided_by=decided_by,
            has_reason=decision_reason is not None,
            reason="lifecycle_baseline_cannot_resume_agent",
        )
        msg = (
            f"Approval {approval_id!r} has a parked agent context but the "
            f"agent runtime is not installed; cannot resume execution."
        )
        raise AgentRuntimeNotConfiguredError(msg)
