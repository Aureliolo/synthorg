"""Backend-side service called by the worker-callable execute endpoint.

When the worker pool fetches a JetStream claim, it posts to
``POST /api/v1/tasks/{task_id}/execute``. The controller delegates to
:class:`WorkerExecutionService.execute_once`, a thin protocol-driven
seam: the controller does not care which implementation is wired, only
that ``execute_once`` returns the post-execution :class:`Task`.

Three implementations live here:

* :class:`AgentEngineExecutionService` -- the real agent runtime.
  Installed at boot behind the provider-present switch (and re-installed
  on setup-reinit); it resolves the assigned agent identity and runs a
  fully-wired :class:`~synthorg.engine.agent_engine.AgentEngine`
  (LLM + tools + per-call sandbox + memory, governed by the SecOps
  safety spine).
* :class:`NoProviderExecutionService` -- empty-company backstop. With no
  provider configured the execute seam fails loudly instead of silently
  walking status labels (task creation is also rejected upstream).
* :class:`LifecycleAdvancingExecutionService` -- the lifecycle-only
  baseline: it advances the task status without invoking an LLM. Used by
  the dispatcher / queue / worker integration tests that pin the claim
  round-trip, and as the property's lazy fallback before the boot hook
  installs the real service.
"""

from typing import TYPE_CHECKING, Final, Protocol

from synthorg.core.domain_errors import (
    AgentRuntimeNotConfiguredError,
    NotFoundError,
)
from synthorg.core.enums import TaskStatus
from synthorg.core.task import (
    Task,  # noqa: TC001 -- runtime Protocol/return-type annotation
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workers import (
    WORKERS_EXECUTION_SERVICE_AGENT_RUN,
    WORKERS_EXECUTION_SERVICE_ATTEMPTED,
    WORKERS_EXECUTION_SERVICE_AUTONOMY_DEGRADED,
    WORKERS_EXECUTION_SERVICE_COMPLETED,
    WORKERS_EXECUTION_SERVICE_NO_OP,
    WORKERS_EXECUTION_SERVICE_NO_PROVIDER,
)

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.engine.agent_engine import AgentEngine
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.hr.registry import AgentRegistryService
    from synthorg.security.autonomy.models import EffectiveAutonomy
    from synthorg.security.autonomy.resolver import AutonomyResolver

logger = get_logger(__name__)

_EXECUTABLE_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
    }
)


class WorkerExecutionService(Protocol):
    """Contract for the worker-callable execution surface.

    The wired implementation is selected by the runtime builder behind
    the provider-present switch (see :mod:`synthorg.workers.runtime_builder`):
    :class:`AgentEngineExecutionService` when a provider is configured,
    :class:`NoProviderExecutionService` otherwise.
    :class:`LifecycleAdvancingExecutionService` is the lifecycle-only
    baseline the dispatcher / queue / worker integration tests pin and
    the property's lazy fallback before the boot hook runs.
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
    """Lifecycle-only :class:`WorkerExecutionService` baseline.

    Advances the task one transition forward when it is in an
    executable state (``ASSIGNED`` or ``IN_PROGRESS``); returns the
    current state unchanged otherwise. No LLM, no tools: a claim
    arrives, the service rolls the lifecycle forward, the worker sees a
    terminal-or-not response and acks accordingly.

    This is what the dispatcher + queue + worker integration tests pin,
    and the lazy fallback the ``AppState.worker_execution_service``
    property self-constructs before the boot hook installs the real
    :class:`AgentEngineExecutionService`. The real agent runtime is a
    sibling class in this module, selected by the runtime builder
    behind the provider-present switch.
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
            logger.warning(
                WORKERS_EXECUTION_SERVICE_NO_OP,
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
        if current in _EXECUTABLE_STATUSES:
            # Defensive: unreachable today but documented contract.
            return None
        return None


class AgentEngineExecutionService:
    """Real agent-runtime :class:`WorkerExecutionService` implementation.

    Resolves the task's assigned agent identity from the registry,
    delegates execution to a fully-wired :class:`AgentEngine` (LLM +
    tools + per-call sandbox + memory, governed by the SecOps safety
    spine), and returns the post-execution task. The engine performs
    its own ``ASSIGNED`` -> ``IN_PROGRESS`` transition and syncs every
    subsequent transition to the ``TaskEngine`` incrementally, so this
    service must NOT pre-walk the lifecycle: it hands the task to the
    engine as-is and re-reads the authoritative post-run state.
    """

    __slots__ = ("_agent_registry", "_autonomy_resolver", "_engine", "_task_engine")

    def __init__(
        self,
        *,
        engine: AgentEngine,
        task_engine: TaskEngine,
        agent_registry: AgentRegistryService,
        autonomy_resolver: AutonomyResolver | None = None,
    ) -> None:
        self._engine = engine
        self._task_engine = task_engine
        self._agent_registry = agent_registry
        self._autonomy_resolver = autonomy_resolver

    async def execute_once(
        self,
        *,
        task_id: str,
        previous_status: str | None,
        new_status: str,
        idempotency_key: str,
        requested_by: str,
    ) -> Task:
        """Run the assigned agent against the task and return its state."""
        task = await self._task_engine.get_task(task_id)
        if task is None:
            logger.warning(
                WORKERS_EXECUTION_SERVICE_NO_OP,
                task_id=task_id,
                reason="task_not_found",
                previous_status=previous_status,
                new_status=new_status,
                idempotency_key=idempotency_key,
            )
            msg = f"Task {task_id!r} not found"
            raise NotFoundError(msg)

        identity = await self._resolve_identity(task.assigned_to, task_id=task_id)
        effective_autonomy = self._resolve_autonomy(identity, task_id=task_id)

        logger.info(
            WORKERS_EXECUTION_SERVICE_ATTEMPTED,
            task_id=task_id,
            current_status=task.status.value,
            agent_id=str(identity.id),
            previous_status=previous_status,
            new_status=new_status,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
        )

        run_result = await self._engine.run(
            identity=identity,
            task=task,
            effective_autonomy=effective_autonomy,
        )
        logger.info(
            WORKERS_EXECUTION_SERVICE_AGENT_RUN,
            task_id=task_id,
            agent_id=str(identity.id),
            termination_reason=run_result.termination_reason.value,
            total_turns=run_result.total_turns,
        )

        # The engine syncs transitions to the TaskEngine itself; re-read
        # the authoritative post-run state for the controller envelope.
        post = await self._task_engine.get_task(task_id)
        if post is None:
            logger.warning(
                WORKERS_EXECUTION_SERVICE_NO_OP,
                task_id=task_id,
                reason="task_missing_post_run",
            )
            msg = f"Task {task_id!r} not found after execution"
            raise NotFoundError(msg)
        logger.info(
            WORKERS_EXECUTION_SERVICE_COMPLETED,
            task_id=task_id,
            from_status=task.status.value,
            to_status=post.status.value,
        )
        return post

    async def _resolve_identity(
        self,
        assigned_to: str | None,
        *,
        task_id: str,
    ) -> AgentIdentity:
        """Resolve the task's assigned agent identity, or raise."""
        if assigned_to:
            identity = await self._agent_registry.get(assigned_to)
            if identity is None:
                identity = await self._agent_registry.get_by_name(assigned_to)
            if identity is not None:
                return identity
        logger.warning(
            WORKERS_EXECUTION_SERVICE_NO_OP,
            task_id=task_id,
            reason="agent_not_registered",
            assigned_to=assigned_to,
        )
        msg = (
            f"Task {task_id!r} is assigned to an agent that is not "
            f"registered in the runtime. Complete setup so agents are "
            f"bootstrapped before submitting work."
        )
        raise AgentRuntimeNotConfiguredError(msg)

    def _resolve_autonomy(
        self,
        identity: AgentIdentity,
        *,
        task_id: str,
    ) -> EffectiveAutonomy | None:
        """Resolve effective autonomy; degrade to ``None`` on misconfig.

        ``None`` still leaves the SecOps rule engine governing every
        tool action (credential / destructive / path-traversal
        detectors plus the approval queue); only the autonomy-tier
        routing layer is skipped.
        """
        if self._autonomy_resolver is None:
            return None
        try:
            return self._autonomy_resolver.resolve(
                agent_level=identity.autonomy_level,
                seniority=identity.level,
            )
        except MemoryError, RecursionError:
            raise
        except ValueError as exc:
            logger.warning(
                WORKERS_EXECUTION_SERVICE_AUTONOMY_DEGRADED,
                task_id=task_id,
                agent_id=str(identity.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None


class NoProviderExecutionService:
    """Empty-company :class:`WorkerExecutionService`.

    Installed when no LLM provider is configured. Task creation is
    already rejected at the submission boundary; this is the
    defence-in-depth backstop so a task that reaches the execute seam
    by any other path fails loudly instead of running ungoverned or
    silently walking status labels.
    """

    __slots__ = ()

    async def execute_once(
        self,
        *,
        task_id: str,
        previous_status: str | None,
        new_status: str,
        idempotency_key: str,
        requested_by: str,
    ) -> Task:
        """Reject execution: the company has no provider configured."""
        logger.warning(
            WORKERS_EXECUTION_SERVICE_NO_PROVIDER,
            task_id=task_id,
            previous_status=previous_status,
            new_status=new_status,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
        )
        msg = (
            "No LLM provider is configured; the company is running in "
            "empty mode and cannot execute tasks. Add a provider in "
            "setup, then resubmit."
        )
        raise AgentRuntimeNotConfiguredError(msg)
