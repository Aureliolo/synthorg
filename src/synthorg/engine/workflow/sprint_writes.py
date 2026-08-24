# module-kind: code
"""What a guarded backlog write's answer means to the caller.

Both writes on a sprint's backlog are one conditional statement, so both
answer the same narrow thing: either the row now carries the change, or
nothing was written. Turning that into what a caller needs is a second
question with a different answer per caller, and it is the same shape twice
(re-read, then decide), so it lives here rather than inside the service the
observer and the REST surface share.

Nothing here holds the service's lock. The completion path re-reads under
it and the REST path re-reads outside it, and neither reads to decide
whether to write: the write already happened or already did not, and the
row is only being asked what it says now.
"""

from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import ConstraintViolationError
from synthorg.core.resilience import GeneralRetryHandler
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    SprintBacklogInvalidError,
    SprintError,
    SprintNotFoundError,
    SprintTransitionConflictError,
)
from synthorg.engine.workflow.sprint_lifecycle import Sprint
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.workflow import (
    SPRINT_BACKLOG_SAVE_FAILED,
    SPRINT_BACKLOG_SAVE_RETRYING,
    SPRINT_COMPLETION_ALREADY_RECORDED,
    SPRINT_COMPLETION_NOT_RECORDED,
)
from synthorg.persistence.sprint_protocol import SprintRepository

logger = get_logger(__name__)

# The completion append is the sprint's source-of-truth write and nothing
# re-fires a task's completion, so a store blip that drops it diverges
# ``completed_task_ids`` from real task state until the recovery sweep or an
# operator notices. Short and bounded: it runs inside the observer's
# critical section, and a store that is genuinely down should surface as a
# failure rather than hold the lock.
_RETRY_MAX_ATTEMPTS: Final[int] = 3
_RETRY_BACKOFF_BASE_SECONDS: Final[float] = 0.05
_RETRY_BACKOFF_CAP_SECONDS: Final[float] = 0.5


async def record_completion(
    sprint: Sprint, task_id: str, *, sprints: SprintRepository
) -> Sprint | None:
    """Record *task_id* as delivered and return the sprint that resulted.

    Args:
        sprint: The sprint as the caller last read it.
        task_id: The delivered task.
        sprints: The durable store, whose guard decides the append.

    Returns:
        The sprint to drive the tail from, or ``None`` when this process
        has nothing to do. A guard that does not match is re-read rather
        than treated as a no-op: it means another writer recorded this
        completion, and if both processes assumed the other would drive
        the tail, neither would.

    Raises:
        Exception: Re-raised after logging. This is the sprint
            source-of-truth write, not an observer side channel: a swallow
            here silently diverges ``completed_task_ids`` from real task
            state with no reconciliation, so it is surfaced distinctly
            (ERROR) before it rides the observer catch-all.
    """

    async def append() -> Sprint | None:
        return await sprints.complete_task_if(
            NotBlankStr(sprint.id), NotBlankStr(task_id)
        )

    retry = GeneralRetryHandler(
        # A refusal is a RESULT here (``None``), never an exception, so every
        # exception this can raise is the store failing rather than the guard
        # declining. Only a constraint violation is excluded: the row's own
        # invariants do not become satisfiable by asking again.
        retryable=lambda exc: not isinstance(exc, ConstraintViolationError),
        max_attempts=_RETRY_MAX_ATTEMPTS,
        base=_RETRY_BACKOFF_BASE_SECONDS,
        cap=_RETRY_BACKOFF_CAP_SECONDS,
        event=SPRINT_BACKLOG_SAVE_RETRYING,
        jitter=False,
    )
    try:
        delivered = await retry.execute(
            append,
            context="sprint backlog completion append",
            sprint_id=sprint.id,
            task_id=task_id,
        )
    except Exception as exc:
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            SPRINT_BACKLOG_SAVE_FAILED,
            exc,
            sprint_id=sprint.id,
            task_id=task_id,
        )
        raise
    if delivered is not None:
        return delivered
    current = await sprints.get(NotBlankStr(sprint.id))
    if current is None or task_id not in current.completed_task_ids:
        logger.warning(
            SPRINT_COMPLETION_NOT_RECORDED,
            sprint_id=sprint.id,
            task_id=task_id,
            sprint_status=current.status.value if current is not None else None,
        )
        return None
    logger.info(
        SPRINT_COMPLETION_ALREADY_RECORDED, sprint_id=sprint.id, task_id=task_id
    )
    return current


async def rejected_add_error(
    sprint_id: str, task_id: str, *, sprints: SprintRepository
) -> SprintError:
    """Name why a backlog-append guard matched nothing.

    The guard answers only "nothing was written", because that is all a
    conditional statement can say. The distinctions a REST caller needs are
    re-read here rather than asked for in SQL: the read is off the write
    path, and a wrong answer costs a misleading message rather than a lost
    task.

    Returned rather than raised so the caller's ``raise`` is at the caller,
    where the control flow reads and where a type checker can see the
    function below it is unreachable.

    Args:
        sprint_id: The sprint the append targeted.
        task_id: The task it tried to add.
        sprints: The durable store, re-read for the reason.

    Returns:
        The error the caller raises: not found when the row has gone,
        invalid when the task is already in the backlog, and a transition
        conflict when the sprint left PLANNING between the check and the
        write.
    """
    current = await sprints.get(NotBlankStr(sprint_id))
    if current is None:
        return SprintNotFoundError(f"Sprint {sprint_id!r} not found")
    if task_id in current.task_ids:
        return SprintBacklogInvalidError(
            f"Task {task_id!r} is already in sprint {sprint_id!r} backlog"
        )
    return SprintTransitionConflictError(
        f"Sprint {sprint_id!r} is not in 'planning'; cannot add tasks"
    )


__all__ = ["record_completion", "rejected_add_error"]
