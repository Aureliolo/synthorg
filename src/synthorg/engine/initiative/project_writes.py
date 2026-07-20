# module-kind: code
"""Version-guarded project writes for the initiative graph.

A project row is written by more than one actor (the pipeline stamping a lead,
the dispatch linking a plan, the rollup advancing status), and by more than one
process. Every write here is therefore optimistic-concurrency guarded: it reads,
computes, and writes with ``expected_version``, and a losing write re-reads and
retries rather than clobbering the winner. This mirrors the staffing write in
``engine/pipeline/service.py``, which established the pattern against the same
repository.

Retrying is safe because each write is idempotent in effect: linking recomputes
the same target plan, and advancing recomputes the same target status from the
same task facts.
"""

from typing import Final
from uuid import UUID

from synthorg.core.persistence_errors import PersistenceVersionConflictError
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.project_transitions import transition_path
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.project import (
    PROJECT_PLAN_LINKED,
    PROJECT_ROLLUP_CONFLICT_EXHAUSTED,
    PROJECT_ROLLUP_CONFLICT_RETRY,
    PROJECT_TRANSITION,
)
from synthorg.persistence.project_protocol import ProjectRepository

logger = get_logger(__name__)

#: Bounded retry budget for a contended project write. A conflict means another
#: actor wrote first; re-reading and recomputing converges, so a small budget is
#: sufficient and keeps a hot row from spinning.
MAX_WRITE_ATTEMPTS: Final[int] = 3


async def link_project_to_plan(
    repository: ProjectRepository,
    *,
    project_id: NotBlankStr,
    plan_id: UUID,
) -> Project | None:
    """Point *project_id* at the plan it is now executing and activate it.

    Called at dispatch, before any task starts, so the graph is connected
    before the first rollup event can observe it. Also used by a replan, where
    the project repoints at the revision that supersedes the previous one.

    Returns:
        The persisted project, or ``None`` when the project no longer exists
        or the write stayed contended for the whole retry budget.
    """
    for attempt in range(1, MAX_WRITE_ATTEMPTS + 1):
        project = await repository.get(project_id)
        if project is None:
            return None
        target = _activation_target(project.status)
        updated = project.model_copy(
            update={
                "plan_id": plan_id,
                "status": target,
                "version": project.version + 1,
            }
        )
        try:
            await repository.update(updated, expected_version=project.version)
        except PersistenceVersionConflictError:
            logger.info(
                PROJECT_ROLLUP_CONFLICT_RETRY,
                project=str(project_id),
                attempt=attempt,
                operation="link",
            )
            continue
        logger.info(
            PROJECT_PLAN_LINKED,
            project=str(project_id),
            plan_id=str(plan_id),
            status=target.value,
        )
        return updated
    logger.warning(
        PROJECT_ROLLUP_CONFLICT_EXHAUSTED,
        project=str(project_id),
        operation="link",
        attempts=MAX_WRITE_ATTEMPTS,
    )
    return None


async def advance_project_status(
    repository: ProjectRepository,
    *,
    project_id: NotBlankStr,
    target: ProjectStatus,
) -> Project | None:
    """Walk *project_id* to *target*, one legal hop at a time.

    The project may be several valid hops from its derived status (a project
    still PLANNING whose plan already completed must pass through ACTIVE), so
    the path is resolved through the state machine rather than assuming a
    single hop. An unreachable target is a no-op, not an error: it means the
    project is terminal or was moved by an operator, and the rollup defers to
    that rather than forcing a status.

    Returns:
        The persisted project, or ``None`` when it no longer exists, the
        target is unreachable, or the write stayed contended.
    """
    for attempt in range(1, MAX_WRITE_ATTEMPTS + 1):
        project = await repository.get(project_id)
        if project is None:
            return None
        if project.status == target:
            return project
        path = transition_path(project.status, target)
        if path is None:
            logger.info(
                PROJECT_TRANSITION,
                project=str(project_id),
                current_state=project.status.value,
                target_state=target.value,
                note="unreachable; leaving the project as the operator set it",
            )
            return project
        updated = project.model_copy(
            update={"status": path[-1], "version": project.version + 1}
        )
        try:
            await repository.update(updated, expected_version=project.version)
        except PersistenceVersionConflictError:
            logger.info(
                PROJECT_ROLLUP_CONFLICT_RETRY,
                project=str(project_id),
                attempt=attempt,
                operation="advance",
            )
            continue
        logger.info(
            PROJECT_TRANSITION,
            project=str(project_id),
            current_state=project.status.value,
            target_state=updated.status.value,
            version=updated.version,
        )
        return updated
    logger.warning(
        PROJECT_ROLLUP_CONFLICT_EXHAUSTED,
        project=str(project_id),
        operation="advance",
        attempts=MAX_WRITE_ATTEMPTS,
    )
    return None


def _activation_target(current: ProjectStatus) -> ProjectStatus:
    """Return the status a project should hold once its plan is dispatched.

    Only a PLANNING project activates. A project an operator paused, cancelled,
    or that already completed keeps its status: dispatching work does not
    override a deliberate operator decision.

    Returns:
        ``ACTIVE`` when *current* is PLANNING, else *current*.
    """
    if current is ProjectStatus.PLANNING:
        return ProjectStatus.ACTIVE
    return current
