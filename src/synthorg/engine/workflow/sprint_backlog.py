"""Sprint backlog management -- pure functions returning new Sprint instances.

All operations are immutable: they return a new ``Sprint`` rather than
mutating the input.
"""

from typing import NoReturn

from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    SPRINT_BACKLOG_INVALID,
    SPRINT_TASK_ADDED,
    SPRINT_TASK_COMPLETED,
    SPRINT_TASK_REMOVED,
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


def remove_task_from_sprint(
    sprint: Sprint,
    task_id: NotBlankStr,
) -> Sprint:
    """Return a new Sprint with a task removed from the backlog.

    Tasks cannot be removed from a COMPLETED sprint.  Story point
    totals are **not** adjusted because per-task point data is not
    stored on the Sprint model.  If the task was previously completed,
    ``story_points_completed`` will retain that task's points, which
    may overstate delivered work.  Callers should manually adjust
    ``story_points_completed`` via ``model_copy`` if needed.

    Args:
        sprint: The current sprint.
        task_id: ID of the task to remove.

    Returns:
        A new Sprint with the task removed.

    Raises:
        ValueError: If the sprint is COMPLETED, or the task is not in
            the backlog.
    """
    if sprint.status is SprintStatus.COMPLETED:
        msg = f"Cannot remove tasks from completed sprint {sprint.id!r}"
        _log_and_raise(
            SPRINT_BACKLOG_INVALID,
            msg,
            sprint_id=sprint.id,
            task_id=task_id,
            reason="completed",
        )
    if task_id not in sprint.task_ids:
        msg = f"Task {task_id!r} is not in sprint {sprint.id!r} backlog"
        _log_and_raise(
            SPRINT_BACKLOG_INVALID,
            msg,
            sprint_id=sprint.id,
            task_id=task_id,
            reason="not_found",
        )
    was_completed = task_id in sprint.completed_task_ids
    new_task_ids = tuple(t for t in sprint.task_ids if t != task_id)
    new_completed = tuple(t for t in sprint.completed_task_ids if t != task_id)
    if was_completed:
        logger.warning(
            SPRINT_BACKLOG_INVALID,
            sprint_id=sprint.id,
            task_id=task_id,
            reason="completed_task_removed_points_stale",
            story_points_committed=sprint.story_points_committed,
            story_points_completed=sprint.story_points_completed,
        )
    result = sprint.model_copy(
        update={
            "task_ids": new_task_ids,
            "completed_task_ids": new_completed,
        },
    )
    logger.info(
        SPRINT_TASK_REMOVED,
        sprint_id=sprint.id,
        task_id=task_id,
    )
    return result


def complete_task_in_sprint(
    sprint: Sprint,
    task_id: NotBlankStr,
    story_points: float,
) -> Sprint:
    """Mark a task as completed within the sprint.

    The task must be in the backlog and not already completed.  The
    sprint must be ACTIVE or IN_REVIEW.

    Args:
        sprint: The current sprint.
        task_id: ID of the task to mark completed.
        story_points: Story points earned for this task.

    Returns:
        A new Sprint with the task marked completed.

    Raises:
        ValueError: If preconditions are not met.
    """
    _validate_completion_preconditions(sprint, task_id, story_points)
    new_completed_points = sprint.story_points_completed + story_points
    result = sprint.model_copy(
        update={
            "completed_task_ids": (
                *sprint.completed_task_ids,
                task_id,
            ),
            "story_points_completed": new_completed_points,
        },
    )
    logger.info(
        SPRINT_TASK_COMPLETED,
        sprint_id=sprint.id,
        task_id=task_id,
        story_points=story_points,
    )
    return result


def _validate_completion_preconditions(
    sprint: Sprint,
    task_id: NotBlankStr,
    story_points: float,
) -> None:
    """Validate preconditions for completing a task in a sprint."""
    allowed = {SprintStatus.ACTIVE, SprintStatus.IN_REVIEW}
    if sprint.status not in allowed:
        msg = (
            f"Cannot complete tasks in sprint {sprint.id!r} "
            f"with status {sprint.status.value!r} -- "
            f"must be 'active' or 'in_review'"
        )
        _log_and_raise(
            SPRINT_BACKLOG_INVALID,
            msg,
            sprint_id=sprint.id,
            task_id=task_id,
            reason="wrong_status",
        )
    if task_id not in sprint.task_ids:
        msg = f"Task {task_id!r} is not in sprint {sprint.id!r} backlog"
        _log_and_raise(
            SPRINT_BACKLOG_INVALID,
            msg,
            sprint_id=sprint.id,
            task_id=task_id,
            reason="not_found",
        )
    if task_id in sprint.completed_task_ids:
        msg = f"Task {task_id!r} is already completed in sprint {sprint.id!r}"
        _log_and_raise(
            SPRINT_BACKLOG_INVALID,
            msg,
            sprint_id=sprint.id,
            task_id=task_id,
            reason="already_completed",
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
    new_completed_points = sprint.story_points_completed + story_points
    if new_completed_points > sprint.story_points_committed:
        msg = (
            f"Completing task {task_id!r} with {story_points} "
            f"points would exceed committed points "
            f"({new_completed_points} > "
            f"{sprint.story_points_committed})"
        )
        _log_and_raise(
            SPRINT_BACKLOG_INVALID,
            msg,
            sprint_id=sprint.id,
            task_id=task_id,
            reason="exceeds_committed",
        )
