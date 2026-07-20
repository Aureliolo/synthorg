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


async def terminate_task(
    task_engine: TaskEngine,
    task: Task,
    *,
    requested_by: str,
    reason: str,
) -> None:
    """Move a non-terminal task to a terminal state.

    The task lifecycle forbids ``CREATED -> CANCELLED`` (a created task is
    rejected, not cancelled) and lets the stuck states (blocked / failed /
    interrupted / suspended) reach a terminal only via ``ASSIGNED``. This
    routes each task to the correct terminal so no live work dangles, and
    every task keeps its audit row.

    Args:
        task_engine: Engine driving the audited status transitions.
        task: The non-terminal task to terminate.
        requested_by: Identity recorded on each transition.
        reason: Why the task is being terminated, recorded on each transition.
    """
    target = (
        TaskStatus.REJECTED
        if task.status is TaskStatus.CREATED
        else TaskStatus.CANCELLED
    )
    if target not in VALID_TRANSITIONS[task.status]:
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
