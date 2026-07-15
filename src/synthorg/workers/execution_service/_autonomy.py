"""Per-initiative autonomy-mode read for the worker execution service."""

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.persistence_errors import MalformedRowError, PersistenceError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workers import (
    WORKERS_EXECUTION_SERVICE_AUTONOMY_DEGRADED,
)
from synthorg.persistence.project_protocol import ProjectRepository

logger = get_logger(__name__)


async def read_project_autonomy_mode(
    project_repo: ProjectRepository | None,
    project_id: NotBlankStr | None,
) -> AutonomyLevel | None:
    """Read the initiative's operator-set autonomy mode.

    Returns ``None`` only when there is genuinely no initiative to read from:
    no project repo wired, no project associated with the task, or the
    associated project exists with no ``autonomy_mode`` set. A ``None`` there
    inherits the department/company default.

    Both a lookup FAILURE and a referenced-but-absent project fail CLOSED to
    ``LOCKED``: an operator may have locked this initiative down precisely
    because the company default is more permissive, so neither an unreadable
    override nor a vanished project row may silently resolve to a looser tier.
    A task that names a ``project_id`` whose row cannot be found is anomalous
    (deleted mid-flight, or a data inconsistency), so it over-gates (extra
    approvals) rather than under-gates (an unattended permissive run).
    ``LOCKED`` is a project-tier value: a per-agent override still takes
    precedence.

    Returns:
        The project's ``autonomy_mode``; ``None`` when no override applies;
        ``AutonomyLevel.LOCKED`` when the lookup failed or the referenced
        project is absent.
    """
    if project_repo is None or project_id is None:
        return None
    try:
        project = await project_repo.get(project_id)
    except PersistenceError as exc:
        # Data corruption is not transient and warrants a louder signal than
        # a connection blip; both still fail closed identically.
        emit = logger.error if isinstance(exc, MalformedRowError) else logger.warning
        emit(
            WORKERS_EXECUTION_SERVICE_AUTONOMY_DEGRADED,
            project_id=project_id,
            reason="project_mode_lookup_failed",
            fail_closed_to=AutonomyLevel.LOCKED.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return AutonomyLevel.LOCKED
    if project is None:
        logger.warning(
            WORKERS_EXECUTION_SERVICE_AUTONOMY_DEGRADED,
            project_id=project_id,
            reason="project_not_found",
            fail_closed_to=AutonomyLevel.LOCKED.value,
        )
        return AutonomyLevel.LOCKED
    return project.autonomy_mode
