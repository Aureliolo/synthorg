"""Runtime task-activity queries shared across analytics surfaces.

The org overview and the per-department health aggregation both express
utilisation as *runtime* busyness (agents mid-task) rather than HR
lifecycle status. Keeping the busy-assignee derivation in one pure helper
stops those two surfaces from drifting apart on what "utilised" means.
"""

from collections.abc import Iterable

from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus


def busy_agent_ids(
    tasks: Iterable[Task],
    candidates: Iterable[str] | None = None,
) -> set[str]:
    """Return the agent ids currently executing a task.

    An agent is busy when it is the assignee of at least one ``IN_PROGRESS``
    task. This is the runtime-state definition of "active" (an idle roster
    yields an empty set), never the HR ``AgentStatus.ACTIVE`` lifecycle flag.

    Args:
        tasks: Tasks to scan.
        candidates: When given, restrict the result to these agent ids (e.g.
            a single department's roster, or the employed set). ``None``
            counts every ``IN_PROGRESS`` assignee.

    Returns:
        The set of busy agent ids.
    """
    candidate_set = frozenset(candidates) if candidates is not None else None
    return {
        task.assigned_to
        for task in tasks
        if task.status == TaskStatus.IN_PROGRESS
        and task.assigned_to
        and (candidate_set is None or task.assigned_to in candidate_set)
    }
