"""Backend-side service called by the worker-callable execute endpoint.

When the worker pool fetches a JetStream claim, it posts to
``POST /api/v1/tasks/{task_id}/execute``. The controller delegates to
:class:`WorkerExecutionService.execute_once` so the agent-runtime
invocation is configurable per deployment: a baseline implementation
walks the task through its lifecycle via the existing :class:`TaskEngine`,
while production deployments override the service to invoke the full
:class:`~synthorg.engine.agent_engine.AgentEngine`.

The service is intentionally a thin protocol-driven seam: the
controller does not care which implementation is wired, only that
``execute_once`` returns the post-execution :class:`Task`. This keeps
the API contract stable while the agent-runtime invocation evolves
across deployments.
"""

from typing import TYPE_CHECKING, Final, Protocol

from synthorg.core.domain_errors import NotFoundError
from synthorg.core.enums import TaskStatus
from synthorg.core.task import (
    Task,  # noqa: TC001 -- runtime Protocol/return-type annotation
)
from synthorg.observability import get_logger
from synthorg.observability.events.workers import (
    WORKERS_EXECUTION_SERVICE_ATTEMPTED,
    WORKERS_EXECUTION_SERVICE_COMPLETED,
    WORKERS_EXECUTION_SERVICE_NO_OP,
)

if TYPE_CHECKING:
    from synthorg.engine.task_engine import TaskEngine

logger = get_logger(__name__)

_EXECUTABLE_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
    }
)


class WorkerExecutionService(Protocol):
    """Contract for the worker-callable execution surface.

    Deployments override this protocol to plug a specific
    agent-runtime invocation. The default implementation
    (:class:`LifecycleAdvancingExecutionService`) walks the task
    forward through the lifecycle without invoking an LLM, which is
    sufficient for smoke tests and for the dispatcher / queue /
    worker / API integration tests that pin the full claim
    round-trip.
    """

    async def execute_once(
        self,
        *,
        task_id: str,
        previous_status: str | None,
        new_status: str,
        idempotency_key: str,
        requested_by: str,
    ) -> Task:
        """Execute one step of the task and return the post-step state.

        Implementations MUST persist the resulting status through the
        ``TaskEngine`` so the single-writer invariant holds, and
        return the typed ``Task`` for the controller to envelope.
        """
        ...


class LifecycleAdvancingExecutionService:
    """Default :class:`WorkerExecutionService` implementation.

    Advances the task one transition forward when it is in an
    executable state (``ASSIGNED`` or ``IN_PROGRESS``); returns the
    current state unchanged otherwise. This is the baseline contract
    the dispatcher + queue + worker tests pin: a claim arrives,
    the service rolls the lifecycle forward, the worker sees a
    terminal-or-not response and acks accordingly.

    Production deployments replace this implementation with one that
    invokes the :class:`~synthorg.engine.agent_engine.AgentEngine`
    against the task body. The agent-engine implementation lives
    outside this baseline because it carries the full agent-runtime
    dependency chain (LLM provider, tool registry, memory backend),
    none of which belong in the dispatch path itself.
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
        """
        task = await self._task_engine.get_task(task_id)
        if task is None:
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
        if current in _EXECUTABLE_STATUSES:
            # Defensive: unreachable today but documented contract.
            return None
        return None
