# module-kind: service
"""Parked-context resume dispatch for the agent-engine execution service.

Extracted from :mod:`synthorg.workers.execution_service` so that module
stays under its size tier while the resume concern (background-task
dispatch, parked-context restore, the taskless chat-action branch, and
the shutdown drain) grows cohesively. Mixed into
:class:`~synthorg.workers.execution_service.AgentEngineExecutionService`,
which provides the boot engine, the background-task registry, and the
autonomy / environment / sandbox helpers this mixin calls through
``self``.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import AgentRuntimeNotConfiguredError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_NO_PARKED_CONTEXT,
    APPROVAL_GATE_RESUME_DISPATCHED,
    APPROVAL_GATE_RESUME_FAILED,
)
from synthorg.observability.events.workers import (
    WORKERS_EXECUTION_SERVICE_FAILED,
)
from synthorg.tools.sandbox.active_environment import (
    ActiveSandboxEnvironment,
    active_sandbox_environment,
)

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.types import NotBlankStr
    from synthorg.engine.agent_engine import AgentEngine
    from synthorg.engine.workspace.project_workspace_service import (
        ProjectWorkspaceService,
    )
    from synthorg.observability.background_tasks import BackgroundTaskRegistry
    from synthorg.security.autonomy.models import EffectiveAutonomy

logger = get_logger(__name__)

# Bounded wait for in-flight parked-context resumes during shutdown
# before they are cancelled, so a slow resume cannot stall process
# teardown indefinitely. Sized to let a typical resumed turn finish
# (a 5s budget routinely cancelled mid-LLM-call); the registry logs a
# WARNING with the pending count on timeout so a cancelled resume is
# never silent.
_RESUME_DRAIN_TIMEOUT_SECONDS: Final[float] = 30.0


class ResumeDispatchMixin:
    """Parked-context resume dispatch for ``AgentEngineExecutionService``.

    Relies on the host execution service for the boot ``AgentEngine``,
    the background-task registry, the project workspace service, and the
    autonomy / environment / sandbox-release helpers (declared below as
    host-provided members so this mixin type-checks in isolation).
    """

    __slots__ = ()

    _engine: AgentEngine
    _resume_tasks: BackgroundTaskRegistry
    _project_workspace_service: ProjectWorkspaceService | None

    # Host-provided helpers (concrete on AgentEngineExecutionService,
    # shared with ``execute_once``). Declared here as abstract stubs so
    # this mixin type-checks in isolation and the host's definitions are
    # recognised as overrides.
    def _resolve_autonomy(
        self,
        identity: AgentIdentity,
        *,
        task_id: str,
    ) -> EffectiveAutonomy | None:
        """Resolve effective autonomy (provided by the host service)."""

    async def _provision_environment(
        self,
        *,
        task_id: str,
        project_id: NotBlankStr | None,
        workspace_path: Path | None,
    ) -> ActiveSandboxEnvironment | None:
        """Provision the project's environment (provided by the host)."""

    async def _release_sandbox_owner(
        self,
        *,
        identity: AgentIdentity,
        task_id: str,
        project_id: str | None,
        image_override: str | None = None,
    ) -> None:
        """Release the sandbox lifecycle owner (provided by the host)."""

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
        _ = self._resume_tasks.spawn(
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

        if ctx.task_execution is None:
            # Taskless chat action (AgentEngine.run_chat_action): no project
            # workspace, sandbox, or task lifecycle applies. Resume the short
            # chat loop directly -- the engine's taskless resume reuses the
            # same governed tool-execution + approval-gate path as the task
            # resume. Everything below this point is project / task keyed and
            # would only no-op for a chat action.
            await self._engine.resume_parked_chat_action(
                parked_context=ctx,
                approval_id=approval_id,
                decision_message=decision_message,
                effective_autonomy=effective_autonomy,
            )
            return

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
