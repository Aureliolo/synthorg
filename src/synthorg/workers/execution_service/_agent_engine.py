# module-kind: service
"""Real agent-runtime worker execution service."""

from pathlib import Path
from typing import TYPE_CHECKING, Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import (
    AgentRuntimeNotConfiguredError,
    ConflictError,
    NotFoundError,
)
from synthorg.core.task import (
    Task,
)
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
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
    WORKERS_EXECUTION_SERVICE_SANDBOX_RELEASE_FAILED,
    WORKERS_EXECUTION_SERVICE_SANDBOX_RELEASED,
    WORKERS_EXECUTION_SERVICE_TASK_NOT_FOUND,
)
from synthorg.observability.events.workspace import ENVIRONMENT_PROVISION_SKIPPED
from synthorg.tools.sandbox.active_environment import (
    ActiveSandboxEnvironment,
    active_sandbox_environment,
)
from synthorg.tools.sandbox.lifecycle.config import (
    STRATEGY_PER_AGENT,
    STRATEGY_PER_CALL,
    STRATEGY_PER_TASK,
)
from synthorg.workers.environment_runner import SandboxEnvironmentRunner

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.types import NotBlankStr
    from synthorg.engine.agent_engine import AgentEngine
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.engine.workspace.environment.service import EnvironmentService
    from synthorg.engine.workspace.project_workspace_service import (
        ProjectWorkspaceService,
    )
    from synthorg.hr.registry import AgentRegistryService
    from synthorg.security.autonomy.models import EffectiveAutonomy
    from synthorg.security.autonomy.resolver import AutonomyResolver
    from synthorg.tools.sandbox.protocol import SandboxBackend

logger = get_logger(__name__)


_RESUME_DRAIN_TIMEOUT_SECONDS: Final[float] = 30.0


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
        "_environment_runner_backend",
        "_environment_service",
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
        environment_service: EnvironmentService | None = None,
        environment_runner_backend: SandboxBackend | None = None,
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
        # Per-project reproducible-environment provisioner + the sandbox
        # backend its setup commands run through (the backend resolved
        # for the build/test tool categories). Both ``None`` when no
        # persistence is wired; ``execute_once`` then skips provisioning.
        self._environment_service = environment_service
        self._environment_runner_backend = environment_runner_backend

    async def _provision_environment(
        self,
        *,
        task_id: str,
        project_id: NotBlankStr | None,
        workspace_path: Path | None,
    ) -> ActiveSandboxEnvironment | None:
        """Provision the project's environment; return the active sandbox env.

        Fail-loud: a provisioning failure is logged and re-raised so a
        broken environment never runs silently.

        Returns:
            The active sandbox environment (image + env additions), or
            ``None`` when no environment service is wired, the task has no
            project, or the workspace was not provisioned.
        """
        if (
            self._environment_service is None
            or self._environment_runner_backend is None
        ):
            return None
        if project_id is None:
            return None
        if workspace_path is None:
            # The environment subsystem is wired and the task has a
            # project, but workspace provisioning did not yield a path
            # (it is best-effort upstream). Surface that the declared
            # environment is NOT being applied rather than skipping mute.
            logger.warning(
                ENVIRONMENT_PROVISION_SKIPPED,
                task_id=task_id,
                project_id=project_id,
                reason="workspace_path_unavailable",
            )
            return None
        runner = SandboxEnvironmentRunner(
            backend=self._environment_runner_backend,
            project_id=project_id,
        )
        try:
            provisioned = await self._environment_service.get_or_provision(
                project_id,
                workspace_path=workspace_path,
                runner=runner,
                sandbox_kind=self._environment_runner_backend.get_backend_type(),
            )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                WORKERS_EXECUTION_SERVICE_FAILED,
                exc,
                task_id=task_id,
                project_id=project_id,
                reason="project_environment_provision_failed",
            )
            raise
        return ActiveSandboxEnvironment(
            image_override=provisioned.image_ref,
            env_additions=dict(provisioned.env_vars),
        )

    async def execute_once(
        self,
        *,
        task_id: str,
        previous_status: str | None,
        new_status: str,
        idempotency_key: str,
        requested_by: str,
    ) -> Task:
        """Run the assigned agent against the task and return its state.

        Returns:
            The authoritative post-run task state re-read from the task
            engine.

        Raises:
            NotFoundError: When the task is missing before or after the
                agent run.
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

        identity = await self._resolve_identity(task.assigned_to, task_id=task_id)
        effective_autonomy = self._resolve_autonomy(identity, task_id=task_id)

        # Lazy per-project workspace provisioning: ensure the project has
        # its persistent git-backed working tree before the agent runs.
        # Skipped when no service is wired (test fixtures, persistence-less
        # dev apps) or the task has no project association.
        workspace_path: Path | None = None
        if self._project_workspace_service is not None and task.project is not None:
            try:
                workspace = await self._project_workspace_service.get_or_provision(
                    task.project
                )
                workspace_path = Path(workspace.workspace_path)
            except Exception as exc:
                reraise_critical(exc)
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

        # Per-project reproducible environment: provision the committed
        # declaration into the workspace before the agent runs, and bind
        # the resulting image + env additions as the active sandbox
        # environment for this run. Fail-loud (log then raise): a broken
        # environment must not present itself as a ready sandbox.
        active_env = await self._provision_environment(
            task_id=task_id,
            project_id=task.project,
            workspace_path=workspace_path,
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
            with active_sandbox_environment(active_env):
                run_result = await self._engine.run(
                    identity=identity,
                    task=task,
                    effective_autonomy=effective_autonomy,
                )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                WORKERS_EXECUTION_SERVICE_FAILED,
                exc,
                task_id=task_id,
                agent_id=str(identity.id),
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
                image_override=active_env.image_override if active_env else None,
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
        """Resolve the task's assigned agent identity, or raise.

        Returns:
            The resolved :class:`AgentIdentity` for the task.

        Raises:
            ConflictError: When the task is unassigned.
            AgentRuntimeNotConfiguredError: When the assigned agent is not
                registered in the runtime.
        """
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
        image_override: str | None = None,
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
            image_override: The reproducible-environment image the run
                executed under, so the release key matches the
                image-suffixed key ``execute`` acquired the container
                under. ``None`` when no per-project environment applied.
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
            await backend.release_owner(
                owner_id, project_id=project_id, image_override=image_override
            )
        except Exception as exc:
            reraise_critical(exc)
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

        Returns:
            The resolved effective autonomy, or ``None`` when no resolver
            is wired or resolution fails (degraded mode).
        """
        if self._autonomy_resolver is None:
            return None
        try:
            return self._autonomy_resolver.resolve(
                agent_level=identity.autonomy_level,
                seniority=identity.level,
            )
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

        Raises:
            AgentRuntimeNotConfiguredError: When the engine has no
                approval gate to resume the parked context with.
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

        # Resumed runs must execute under the same provisioned image / env
        # additions as the original run, or reproducibility breaks across
        # the pause/resume boundary. Mirror execute_once: best-effort
        # workspace provisioning, then bind the active sandbox environment.
        workspace_path: Path | None = None
        if self._project_workspace_service is not None and project_id is not None:
            try:
                workspace = await self._project_workspace_service.get_or_provision(
                    project_id
                )
                workspace_path = Path(workspace.workspace_path)
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    WORKERS_EXECUTION_SERVICE_FAILED,
                    task_id=task_id,
                    project_id=project_id,
                    reason="project_workspace_provision_failed",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        active_env = await self._provision_environment(
            task_id=task_id,
            project_id=project_id,
            workspace_path=workspace_path,
        )
        try:
            with active_sandbox_environment(active_env):
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
                image_override=active_env.image_override if active_env else None,
            )

    async def drain_resume_tasks(
        self,
        *,
        timeout_sec: float = _RESUME_DRAIN_TIMEOUT_SECONDS,
    ) -> None:
        """Wait for in-flight parked-context resumes (shutdown hook)."""
        await self._resume_tasks.drain(timeout_sec=timeout_sec)
