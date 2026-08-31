"""Sprint backlog assembly -- pure functions returning new Sprint instances.

All operations are immutable: they return a new ``Sprint`` rather than
mutating the input.

Assembly only. Recording a task as *delivered* is not here, because it
happens while the sprint is running and two processes can reach it at
once: it lives in ``SprintRepository.complete_task_if``, one conditional
statement whose guard is the row's own current value. A pure function
cannot express that, and a second in-memory way to perform the same
mutation would be a way to bypass it.
"""

from typing import NoReturn

from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    SPRINT_BACKLOG_INVALID,
    SPRINT_TASK_ADDED,
)

logger = get_logger(__name__)


def _log_and_raise(event: str, msg: str, **kwargs: object) -> NoReturn:
    """Log a warning with *event* and structured *kwargs*, then raise ``ValueError``.

    Raises:
        ValueError: Always; the function exists to centralise the
            log + raise pair.
    """
    logger.warning(event, **kwargs)
    raise ValueError(msg)


def add_task_to_sprint(
    sprint: Sprint,
    task_id: NotBlankStr,
    story_points: float = 0.0,
) -> Sprint:
    """Return a new Sprint with a task added to the backlog.

    Tasks can only be added during PLANNING.  Enforcement of
    ``SprintConfig.max_tasks_per_sprint`` is the caller's responsibility;
    this function does not receive config context.

    Args:
        sprint: The current sprint.
        task_id: ID of the task to add.
        story_points: Story points for the task.

    Returns:
        A new Sprint with the task added.

    Raises:
        ValueError: If the sprint is not in PLANNING status, or the
            task is already in the backlog, or story_points is negative.
    """
    if sprint.status is not SprintStatus.PLANNING:
        msg = (
            f"Cannot add tasks to sprint {sprint.id!r} in status "
            f"{sprint.status.value!r} -- must be 'planning'"
        )
        _log_and_raise(
            SPRINT_BACKLOG_INVALID,
            msg,
            sprint_id=sprint.id,
            task_id=task_id,
            reason="wrong_status",
        )
    if task_id in sprint.task_ids:
        msg = f"Task {task_id!r} is already in sprint {sprint.id!r} backlog"
        _log_and_raise(
            SPRINT_BACKLOG_INVALID,
            msg,
            sprint_id=sprint.id,
            task_id=task_id,
            reason="duplicate",
        )
    if story_points < 0:
        msg = f"story_points must be >= 0, got {story_points}"
        _log_and_raise(
            SPRINT_BACKLOG_INVALID,
            msg,
            sprint_id=sprint.id,
            task_id=task_id,
            reason="negative_points",
        )
    result = sprint.model_copy(
        update={
            "task_ids": (*sprint.task_ids, task_id),
            "task_points": {**sprint.task_points, task_id: story_points},
            "story_points_committed": (sprint.story_points_committed + story_points),
        },
    )
    logger.info(
        SPRINT_TASK_ADDED,
        sprint_id=sprint.id,
        task_id=task_id,
        story_points=story_points,
    )
    return result
