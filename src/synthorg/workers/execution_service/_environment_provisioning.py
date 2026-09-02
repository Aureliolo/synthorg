"""Provision a project's declared environment ahead of a run.

Fail-loud: a provisioning failure is logged and re-raised so a broken
environment never runs silently, and a workspace that was never provisioned
is reported rather than skipped mute, because the declared environment is
then NOT being applied.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.workers import WORKERS_EXECUTION_SERVICE_FAILED
from synthorg.observability.events.workspace import ENVIRONMENT_PROVISION_SKIPPED
from synthorg.tools.sandbox.active_environment import ActiveSandboxEnvironment
from synthorg.tools.sandbox.protocol import SandboxBackend
from synthorg.workers.environment_runner import SandboxEnvironmentRunner

if TYPE_CHECKING:
    from synthorg.engine.workspace.environment.service import EnvironmentService

logger = get_logger(__name__)


async def provision_environment(
    *,
    environment_service: EnvironmentService,
    runner_backend: SandboxBackend,
    task_id: str,
    project_id: NotBlankStr,
    workspace_path: Path | None,
) -> ActiveSandboxEnvironment | None:
    """Provision *project_id*'s environment; return the active sandbox env.

    Returns:
        The active sandbox environment (image + env additions), or ``None``
        when the workspace was not provisioned.

    Raises:
        Exception: Whatever provisioning raised, after it has been logged.
    """
    if workspace_path is None:
        # The environment subsystem is wired and the task has a project, but
        # workspace provisioning did not yield a path (it is best-effort
        # upstream).
        logger.warning(
            ENVIRONMENT_PROVISION_SKIPPED,
            task_id=task_id,
            project_id=project_id,
            reason="workspace_path_unavailable",
        )
        return None
    runner = SandboxEnvironmentRunner(backend=runner_backend, project_id=project_id)
    try:
        provisioned = await environment_service.get_or_provision(
            project_id,
            workspace_path=workspace_path,
            runner=runner,
            sandbox_kind=runner_backend.get_backend_type(),
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


__all__ = ["provision_environment"]
