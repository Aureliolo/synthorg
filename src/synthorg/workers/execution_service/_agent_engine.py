# module-kind: service
"""Real agent-runtime worker execution service."""

from pathlib import Path
from typing import TYPE_CHECKING, override

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import (
    AgentRuntimeNotConfiguredError,
    ConflictError,
    NotFoundError,
)
from synthorg.core.task import (
    Task,
)
from synthorg.core.types import NotBlankStr
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.background_tasks import BackgroundTaskRegistry
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
from synthorg.tools.sandbox.protocol import SandboxBackend
from synthorg.workers.environment_runner import SandboxEnvironmentRunner
from synthorg.workers.execution_resume import ResumeDispatchMixin

if TYPE_CHECKING:
    from synthorg.engine.agent_engine import AgentEngine
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.engine.workspace.environment.service import EnvironmentService
    from synthorg.engine.workspace.project_workspace_service import (
        ProjectWorkspaceService,
    )
    from synthorg.hr.registry import AgentRegistryService
    from synthorg.security.autonomy.models import EffectiveAutonomy
    from synthorg.security.autonomy.resolver import AutonomyResolver

logger = get_logger(__name__)


class AgentEngineExecutionService(ResumeDispatchMixin):
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

    @property
    def engine(self) -> AgentEngine:
        """The boot ``AgentEngine`` this service runs.

        Shared with the multi-agent coordinator and reused by the
        direct-MCP conversational actor so a parked chat action resumes
        on the same ``ApprovalGate`` the ``/approvals`` controller drives.
        """
        return self._engine

    @property
    def autonomy_resolver(self) -> AutonomyResolver | None:
        """The autonomy resolver this service resolves effective tiers with.

        Reused by the conversational actor so a chat-driven action and a
        worker-run task resolve identical effective autonomy for the same
        agent.
        """
        return self._autonomy_resolver

    @override
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
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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

    @override
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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

    @override
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
