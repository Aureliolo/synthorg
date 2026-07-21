# module-kind: code
"""Route a live task to a terminal state through its audited lifecycle.

Shared by the teardowns that retire work wholesale: deleting a project, and
replanning a dispatched plan out from under the tasks building it. Both must
leave no live task pointing at something that no longer exists, and both must
get there through real transitions so every task keeps its audit row.
"""

from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.task_transitions import VALID_TRANSITIONS
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_apply_helpers import TRULY_TERMINAL_STATUSES


async def terminate_task(
    task_engine: TaskEngine,
    task: Task,
    *,
    requested_by: str,
    reason: str,
) -> TaskStatus | None:
    """Move a live task to a terminal state, returning where it landed.

    The task lifecycle forbids ``CREATED -> CANCELLED`` (a created task is
    rejected, not cancelled) and lets the stuck states (blocked / failed /
    interrupted / suspended) reach a terminal only via ``ASSIGNED``. This
    routes each task to the correct terminal so no live work dangles, and
    every task keeps its audit row.

    The caller's *task* is a snapshot that may predate a concurrent
    completion (a teardown drains every page before terminating any row, so
    a task can finish or be cancelled in between). The current status is
    re-read here so the terminal is chosen from live state and an
    already-terminal task is skipped rather than driven through an invalid
    transition that would abort the teardown mid-loop.

    Args:
        task_engine: Engine driving the audited status transitions.
        task: The task to terminate, possibly a stale snapshot.
        requested_by: Identity recorded on each transition.
        reason: Why the task is being terminated, recorded on each transition.

    Returns:
        The terminal status reached (``REJECTED`` for a created task,
        ``CANCELLED`` otherwise), or ``None`` if the task was already
        terminal or no longer exists.
    """
    current = await task_engine.get_task(str(task.id))
    if current is None or current.status in TRULY_TERMINAL_STATUSES:
        return None
    target = (
        TaskStatus.REJECTED
        if current.status is TaskStatus.CREATED
        else TaskStatus.CANCELLED
    )
    if target not in VALID_TRANSITIONS[current.status]:
        # A stuck state can only reach a terminal through ASSIGNED; hop there
        # first (the task keeps its assignee), then cancel.
        await task_engine.transition_task(
            str(task.id),
            TaskStatus.ASSIGNED,
            requested_by=requested_by,
            reason=reason,
        )
        target = TaskStatus.CANCELLED
    await task_engine.transition_task(
        str(task.id),
        target,
        requested_by=requested_by,
        reason=reason,
    )
    return target
