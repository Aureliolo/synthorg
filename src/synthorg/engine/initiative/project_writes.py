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

See docs/reference/retry-patterns.md: Pattern C/CAS. The loop is hand-rolled
rather than driven through ``CASRetryHandler`` because the repository raises
the persistence-layer ``PersistenceVersionConflictError`` (the handler catches
the API-boundary twin) and because advancing has branches that skip the write
entirely as well as a walk that writes once per hop.
"""

from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.lifecycle_transition import LifecycleEntityKind
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
    PROJECT_WRITE_TARGET_MISSING,
)
from synthorg.persistence.lifecycle_ledger import LifecycleLedger
from synthorg.persistence.project_protocol import ProjectRepository

logger = get_logger(__name__)

#: Bounded retry budget for a contended project write. A conflict means another
#: actor wrote first; re-reading and recomputing converges, so a small budget is
#: sufficient and keeps a hot row from spinning.
MAX_WRITE_ATTEMPTS: Final[int] = 3


class ProjectAdvance(BaseModel):
    """The outcome of one advance, with the status it actually found.

    Attributes:
        project: The project after the walk, or ``None`` when it no longer
            exists or the write stayed contended.
        before: The status observed on the read the winning write was computed
            from. Callers that fire once on an edge into a status must test
            against this, not against a read of their own: a separate read can
            be overtaken between the two calls, and the edge would be missed
            exactly when it mattered.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project: Project | None = Field(default=None, description="Project after the walk")
    before: ProjectStatus | None = Field(
        default=None,
        description="Status observed before the walk",
    )


async def link_project_to_plan(
    repository: ProjectRepository,
    *,
    project_id: NotBlankStr,
    plan_id: UUID,
    ledger: LifecycleLedger | None = None,
) -> Project | None:
    """Point *project_id* at the plan it is now executing and activate it.

    Called at dispatch, before any task starts, so the graph is connected
    before the first rollup event can observe it. Also called by a re-plan, to
    repoint the project at the revision that supersedes the retired one; the
    activation is a no-op there, since the project is already live.

    Args:
        repository: The project store.
        project_id: The project to repoint.
        plan_id: The plan it now executes.
        ledger: Records the activation hop when the link activates a
            PLANNING project.

    Returns:
        The persisted project, or ``None`` when the project no longer exists
        or the write stayed contended for the whole retry budget.
    """
    for attempt in range(1, MAX_WRITE_ATTEMPTS + 1):
        project = await repository.get(project_id)
        if project is None:
            logger.warning(
                PROJECT_WRITE_TARGET_MISSING,
                project=str(project_id),
                operation="link",
            )
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
        if ledger is not None and target is not project.status:
            await ledger.record(
                entity_kind=LifecycleEntityKind.PROJECT,
                entity_id=project_id,
                from_status=project.status.value,
                to_status=NotBlankStr(target.value),
                entity_version=updated.version,
                reason="plan dispatched",
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
    ledger: LifecycleLedger | None = None,
) -> ProjectAdvance:
    """Walk *project_id* to *target*, persisting one legal hop at a time.

    The project may be several valid hops from its derived status (a project
    still PLANNING whose plan already completed must pass through ACTIVE), so
    the path is resolved through the state machine and every hop is persisted
    in its own version-guarded write. Writing the endpoint directly would
    store a transition the state machine rejects and lose the intermediate
    hop from the audit trail.

    The whole walk completes within this call. The caller is the rollup, which
    stops recomputing once the plan is terminal, so there is no later event to
    carry an unfinished walk forward.

    An unreachable target is a no-op, not an error: it means the project is
    terminal or was moved by an operator, and the rollup defers to that rather
    than forcing a status.

    Args:
        repository: The project store.
        project_id: The project to walk.
        target: The status the walk aims for.
        ledger: Records every persisted hop, so the intermediate states the
            walk passes through survive the process that wrote them.

    Returns:
        The walk's outcome, carrying the persisted project (``None`` when it
        no longer exists or the write stayed contended) and the status it was
        found in.
    """
    for attempt in range(1, MAX_WRITE_ATTEMPTS + 1):
        project = await repository.get(project_id)
        if project is None:
            logger.warning(
                PROJECT_WRITE_TARGET_MISSING,
                project=str(project_id),
                operation="advance",
            )
            return ProjectAdvance()
        before = project.status
        if project.status == target:
            return ProjectAdvance(project=project, before=before)
        path = transition_path(project.status, target)
        if path is None:
            logger.info(
                PROJECT_TRANSITION,
                project=str(project_id),
                current_state=project.status.value,
                target_state=target.value,
                note="unreachable; leaving the project as the operator set it",
            )
            return ProjectAdvance(project=project, before=before)
        walked = await _walk_hops(repository, project, path, ledger)
        if walked is not None:
            return ProjectAdvance(project=walked, before=before)
        logger.info(
            PROJECT_ROLLUP_CONFLICT_RETRY,
            project=str(project_id),
            attempt=attempt,
            operation="advance",
        )
    logger.warning(
        PROJECT_ROLLUP_CONFLICT_EXHAUSTED,
        project=str(project_id),
        operation="advance",
        attempts=MAX_WRITE_ATTEMPTS,
    )
    return ProjectAdvance()


async def _walk_hops(
    repository: ProjectRepository,
    project: Project,
    path: tuple[ProjectStatus, ...],
    ledger: LifecycleLedger | None,
) -> Project | None:
    """Persist each hop of *path* in its own version-guarded write.

    A hop that loses its write abandons the walk rather than skipping ahead;
    hops already persisted are legal transitions and stay, and the caller
    restarts from a fresh read.

    Returns:
        The project after the final hop, or ``None`` when a concurrent write
        won and the walk must be restarted.
    """
    current = project
    for hop in path:
        updated = current.model_copy(
            update={"status": hop, "version": current.version + 1}
        )
        try:
            await repository.update(updated, expected_version=current.version)
        except PersistenceVersionConflictError:
            return None
        logger.info(
            PROJECT_TRANSITION,
            project=str(current.id),
            current_state=current.status.value,
            target_state=hop.value,
            version=updated.version,
        )
        if ledger is not None:
            await ledger.record(
                entity_kind=LifecycleEntityKind.PROJECT,
                entity_id=NotBlankStr(str(current.id)),
                from_status=current.status.value,
                to_status=NotBlankStr(hop.value),
                entity_version=updated.version,
            )
        current = updated
    return current


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
