"""Backend-side service called by the worker-callable execute endpoint.

When the worker pool fetches a JetStream claim, it posts to
``POST /api/v1/tasks/{task_id}/execute``. The controller delegates to
:class:`WorkerExecutionService.execute_once`, a thin protocol-driven
seam: the controller does not care which implementation is wired, only
that ``execute_once`` returns the post-execution :class:`Task`.

:mod:`synthorg.workers.runtime_builder` selects the implementation
behind the provider-present switch (``AgentEngineExecutionService``
when a provider is configured, ``NoProviderExecutionService``
otherwise) and installs it through the
``AppState.worker_execution_service`` seam.

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
  round-trip, and the implementation the
  ``AppState.worker_execution_service`` property lazily self-constructs
  when no explicit service has been installed.
"""

from typing import TYPE_CHECKING, Final, Protocol

from synthorg.core.domain_errors import (
    AgentRuntimeNotConfiguredError,
    ConflictError,
    NotFoundError,
)
from synthorg.core.enums import TaskStatus
from synthorg.core.task import (
    Task,  # noqa: TC001 -- runtime Protocol/return-type annotation
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.background_tasks import BackgroundTaskRegistry
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_NO_PARKED_CONTEXT,
    APPROVAL_GATE_RESUME_DISPATCHED,
    APPROVAL_GATE_RESUME_FAILED,
)
from synthorg.observability.events.workers import (
    WORKERS_EXECUTION_SERVICE_AGENT_RUN,
    WORKERS_EXECUTION_SERVICE_ATTEMPTED,
    WORKERS_EXECUTION_SERVICE_AUTONOMY_DEGRADED,
    WORKERS_EXECUTION_SERVICE_COMPLETED,
    WORKERS_EXECUTION_SERVICE_FAILED,
    WORKERS_EXECUTION_SERVICE_NO_OP,
    WORKERS_EXECUTION_SERVICE_NO_PROVIDER,
    WORKERS_EXECUTION_SERVICE_SANDBOX_RELEASE_FAILED,
    WORKERS_EXECUTION_SERVICE_SANDBOX_RELEASED,
    WORKERS_EXECUTION_SERVICE_TASK_NOT_FOUND,
)
from synthorg.tools.sandbox.lifecycle.config import (
    STRATEGY_PER_AGENT,
    STRATEGY_PER_CALL,
    STRATEGY_PER_TASK,
)

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.engine.agent_engine import AgentEngine
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.engine.workspace.project_workspace_service import (
        ProjectWorkspaceService,
    )
    from synthorg.hr.registry import AgentRegistryService
    from synthorg.security.autonomy.models import EffectiveAutonomy
    from synthorg.security.autonomy.resolver import AutonomyResolver
    from synthorg.tools.sandbox.protocol import SandboxBackend

logger = get_logger(__name__)

# Bounded wait for in-flight parked-context resumes during shutdown
# before they are cancelled, so a slow resume cannot stall process
# teardown indefinitely. Sized to let a typical resumed turn finish
# (a 5s budget routinely cancelled mid-LLM-call); the registry logs a
# WARNING with the pending count on timeout so a cancelled resume is
# never silent.
_RESUME_DRAIN_TIMEOUT_SECONDS: Final[float] = 30.0


class WorkerExecutionService(Protocol):
    """Contract for the worker-callable execution surface.

    The wired implementation is selected by the runtime builder behind
    the provider-present switch (see :mod:`synthorg.workers.runtime_builder`):
    :class:`AgentEngineExecutionService` when a provider is configured,
    :class:`NoProviderExecutionService` otherwise.
    :class:`LifecycleAdvancingExecutionService` is the lifecycle-only
    baseline the dispatcher / queue / worker integration tests pin and
    the property's lazy fallback when no explicit service is installed.
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

    async def dispatch_resume(
        self,
        *,
        approval_id: str,
        approved: bool,
        decided_by: str,
        decision_reason: str | None,
    ) -> None:
        """Schedule a parked-context resume off the request path.

        Called by the ``/approvals`` controller once a decision is
        persisted and a parked context is known to exist. The agent
        runtime implementation restores the parked ``AgentContext`` via
        the shared ``ApprovalGate``, injects the decision, and
        continues the original run as a tracked background task,
        returning immediately so the approve/reject HTTP response is
        not blocked by a full agent re-run. Non-runtime implementations
        reject loudly: a parked context with no agent engine to resume
        it is a misconfiguration, not a no-op.
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


class AgentEngineExecutionService:
    """Real agent-runtime :class:`WorkerExecutionService` implementation.

    Resolves the task's assigned agent identity from the registry,
    delegates execution to a fully-wired :class:`AgentEngine` (LLM +
    tools + per-call sandbox + memory, governed by the SecOps safety
    spine), and returns the post-execution task. The engine performs
    its own ``ASSIGNED`` -> ``IN_PROGRESS`` transition and syncs its
    post-execution transitions to the ``TaskEngine``, so this service
    must NOT pre-walk the lifecycle: it hands the task to the engine
    as-is and re-reads the authoritative post-run state.
    """

    __slots__ = (
        "_agent_registry",
        "_autonomy_resolver",
        "_engine",
        "_lifecycle_strategy_kind",
        "_project_workspace_service",
        "_resume_tasks",
        "_sandbox_backend",
        "_task_engine",
    )

    def __init__(  # noqa: PLR0913
        self,
        *,
        engine: AgentEngine,
        task_engine: TaskEngine,
        agent_registry: AgentRegistryService,
        autonomy_resolver: AutonomyResolver | None = None,
        sandbox_backend: SandboxBackend | None = None,
        lifecycle_strategy_kind: str = STRATEGY_PER_CALL,
        project_workspace_service: ProjectWorkspaceService | None = None,
    ) -> None:
        self._engine = engine
        self._task_engine = task_engine
        self._agent_registry = agent_registry
        self._autonomy_resolver = autonomy_resolver
        # Parked-context resumes run off the approve/reject request
        # path so the HTTP response is not blocked by a full agent
        # re-run. Tracked so a crashed resume surfaces in logs and is
        # drained on shutdown instead of vanishing as a GC warning.
        self._resume_tasks = BackgroundTaskRegistry(owner="approval.resume")
        # Sandbox backend whose lifecycle owner is released at the task
        # boundary.  ``None`` when no Docker backend is wired (e.g. an
        # all-subprocess config); release is then skipped entirely.
        self._sandbox_backend = sandbox_backend
        self._lifecycle_strategy_kind = lifecycle_strategy_kind
        # Per-project persistent workspace provisioner. ``None`` for
        # deployments without persistence; ``execute_once`` then skips
        # the lazy provision.
        self._project_workspace_service = project_workspace_service

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
                WORKERS_EXECUTION_SERVICE_TASK_NOT_FOUND,
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

        # Lazy per-project workspace provisioning: ensure the project has
        # its persistent git-backed working tree before the agent runs.
        # Skipped when no service is wired (test fixtures, persistence-less
        # dev apps) or the task has no project association.
        if self._project_workspace_service is not None and task.project is not None:
            try:
                await self._project_workspace_service.get_or_provision(task.project)
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                # Best-effort: workspace provisioning failure should not
                # block agent execution (the workspace may not be needed
                # by every tool). Log and continue.
                logger.warning(
                    WORKERS_EXECUTION_SERVICE_FAILED,
                    task_id=task_id,
                    project_id=task.project,
                    reason="project_workspace_provision_failed",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

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

        try:
            run_result = await self._engine.run(
                identity=identity,
                task=task,
                effective_autonomy=effective_autonomy,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.error(
                WORKERS_EXECUTION_SERVICE_FAILED,
                task_id=task_id,
                agent_id=str(identity.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        finally:
            # Release the sandbox lifecycle owner at the task boundary
            # regardless of run outcome: per-task destroys the container
            # now, per-agent starts the grace timer (a subsequent task
            # for the same agent within the window re-acquires the warm
            # container), per-call is a no-op.
            await self._release_sandbox_owner(
                identity=identity,
                task_id=task_id,
                project_id=task.project,
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
                WORKERS_EXECUTION_SERVICE_TASK_NOT_FOUND,
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
        if not assigned_to:
            logger.warning(
                WORKERS_EXECUTION_SERVICE_NO_OP,
                task_id=task_id,
                reason="task_unassigned",
            )
            msg = f"Task {task_id!r} is not assigned to any agent."
            raise ConflictError(msg)

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

    async def _release_sandbox_owner(
        self,
        *,
        identity: AgentIdentity,
        task_id: str,
        project_id: str | None,
    ) -> None:
        """Release the sandbox lifecycle owner at the task boundary.

        Picks the owner key matching the configured strategy
        (``agent_id`` for per-agent, ``task_id`` for per-task) and
        dispatches to the backend.  Per-call needs no release.  Failures
        are logged and swallowed: sandbox teardown must never fail an
        otherwise-successful task.

        ``project_id`` is passed explicitly (not derived from the
        correlation context) because this release fires AFTER the
        engine's ``correlation_scope`` has exited; the Docker backend
        prefixes the owner key with it so the release matches the
        project-prefixed key ``execute`` acquired the container under.

        Args:
            identity: The agent that ran the task.
            task_id: The task that just completed.
            project_id: The project the task ran under (matches the
                sandbox mount + lifecycle key prefix).
        """
        backend = self._sandbox_backend
        if backend is None:
            return
        if self._lifecycle_strategy_kind == STRATEGY_PER_AGENT:
            owner_id = str(identity.id)
        elif self._lifecycle_strategy_kind == STRATEGY_PER_TASK:
            owner_id = task_id
        else:
            return
        try:
            await backend.release_owner(owner_id, project_id=project_id)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                WORKERS_EXECUTION_SERVICE_SANDBOX_RELEASE_FAILED,
                task_id=task_id,
                agent_id=str(identity.id),
                strategy=self._lifecycle_strategy_kind,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return
        logger.info(
            WORKERS_EXECUTION_SERVICE_SANDBOX_RELEASED,
            task_id=task_id,
            agent_id=str(identity.id),
            strategy=self._lifecycle_strategy_kind,
            owner_id=owner_id,
        )

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

    async def dispatch_resume(
        self,
        *,
        approval_id: str,
        approved: bool,
        decided_by: str,
        decision_reason: str | None,
    ) -> None:
        """Spawn the parked-context resume as a tracked background task.

        Returns immediately; the resume restores the parked context
        via the engine's shared ``ApprovalGate``, injects the decision,
        and continues the original run. Failures surface through the
        registry's done-callback (and the resumed run's own task-status
        sync), never as a blocked approve/reject response.
        """
        logger.info(
            APPROVAL_GATE_RESUME_DISPATCHED,
            approval_id=approval_id,
            approved=approved,
            decided_by=decided_by,
            has_reason=decision_reason is not None,
        )
        self._resume_tasks.spawn(
            self._resume_parked(
                approval_id=approval_id,
                approved=approved,
                decided_by=decided_by,
                decision_reason=decision_reason,
            ),
            event=APPROVAL_GATE_RESUME_FAILED,
            approval_id=approval_id,
            approved=approved,
            decided_by=decided_by,
        )

    async def _resume_parked(
        self,
        *,
        approval_id: str,
        approved: bool,
        decided_by: str,
        decision_reason: str | None,
    ) -> None:
        """Restore the parked context and continue the original run.

        Uses the engine's injected (boot-shared) ``ApprovalGate`` so
        the load+delete here and the park on the engine side operate on
        one gate over one ``ParkedContextRepository``. ``resume_context``
        consumes the parked record; if the subsequent run fails it is
        funnelled through the engine's fatal/budget handlers which sync
        an authoritative terminal task state, leaving the task
        re-runnable by a normal dispatch rather than wedged.
        """
        gate = self._engine._approval_gate  # noqa: SLF001
        if gate is None:
            # The decision is already persisted by the controller, so
            # returning here would strand the parked run permanently
            # (a successful-looking no-op). Raise so the background
            # registry records a real failure the operator can act on.
            logger.error(
                APPROVAL_GATE_RESUME_FAILED,
                approval_id=approval_id,
                reason="engine_has_no_approval_gate",
            )
            msg = (
                f"Approval {approval_id!r} has a parked context but the "
                f"agent engine has no approval gate; cannot resume "
                f"execution."
            )
            raise AgentRuntimeNotConfiguredError(msg)
        resumed = await gate.resume_context(approval_id)
        if resumed is None:
            # The decision is already persisted by the controller, so
            # "approved but no parked context to resume" is an
            # operationally meaningful dead-end (the agent will never
            # pick the work back up), not a routine event: log WARNING
            # so it is visible, not buried at INFO.
            logger.warning(
                APPROVAL_GATE_NO_PARKED_CONTEXT,
                approval_id=approval_id,
                approved=approved,
                decided_by=decided_by,
                note="resume dispatched but no parked context found; "
                "decision persisted, agent will not resume",
            )
            return
        ctx, _ = resumed
        decision_message = gate.build_resume_message(
            approval_id,
            approved=approved,
            decided_by=decided_by,
            decision_reason=decision_reason,
        )
        task_id = ctx.task_execution.task.id if ctx.task_execution else ""
        project_id = ctx.task_execution.task.project if ctx.task_execution else None
        effective_autonomy = self._resolve_autonomy(
            ctx.identity,
            task_id=task_id,
        )
        try:
            await self._engine.resume_parked_run(
                parked_context=ctx,
                approval_id=approval_id,
                decision_message=decision_message,
                effective_autonomy=effective_autonomy,
            )
        finally:
            # The resumed run can acquire a reusable sandbox container
            # just like execute_once(); release the lifecycle owner at
            # this boundary too (regardless of outcome) so per-task
            # destroys it now and per-agent starts its grace timer.
            await self._release_sandbox_owner(
                identity=ctx.identity,
                task_id=task_id,
                project_id=project_id,
            )

    async def drain_resume_tasks(
        self,
        *,
        timeout_sec: float = _RESUME_DRAIN_TIMEOUT_SECONDS,
    ) -> None:
        """Wait for in-flight parked-context resumes (shutdown hook)."""
        await self._resume_tasks.drain(timeout_sec=timeout_sec)


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

    async def dispatch_resume(
        self,
        *,
        approval_id: str,
        approved: bool,
        decided_by: str,
        decision_reason: str | None,
    ) -> None:
        """Reject: no provider means no agent engine to resume into.

        A parked context implies an ``AgentEngine`` ran before the
        provider was removed; surfacing this loudly tells the operator
        the deployment is misconfigured rather than silently dropping
        an approved resume.
        """
        logger.error(
            APPROVAL_GATE_RESUME_FAILED,
            approval_id=approval_id,
            approved=approved,
            decided_by=decided_by,
            has_reason=decision_reason is not None,
            reason="no_provider_cannot_resume_agent",
        )
        msg = (
            f"Approval {approval_id!r} has a parked agent context but no "
            f"LLM provider is configured; cannot resume execution. "
            f"Restore the provider, then retry the decision."
        )
        raise AgentRuntimeNotConfiguredError(msg)
