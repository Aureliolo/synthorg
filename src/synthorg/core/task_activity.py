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
    running: Iterable[str] | None = None,
) -> set[str]:
    """Return the agent ids working right now.

    Two facts make an agent busy and the org has both, so this reads both.
    The first is the task board: an agent assigned at least one ``IN_PROGRESS``
    task. The second is the live agent-state row, written for the duration of
    every agent run and cleared when it ends.

    Reading the board alone is not the same claim, and the gap is not a corner
    case. A decomposition planning session runs as a staffed roster agent for
    turns at a time against a real provider bill, and moves no task to
    ``IN_PROGRESS``: the objective it is planning stays at ``CREATED`` until
    dispatch. A live run spent 54 minutes there while every surface built on
    the board alone reported ``0 active`` and the whole roster idle, beside a
    Live Activity feed listing the planner's API calls one after another.

    Neither is the HR ``AgentStatus.ACTIVE`` lifecycle flag: an idle roster
    still yields an empty set.

    Args:
        tasks: Tasks to scan.
        candidates: When given, restrict the result to these agent ids (e.g.
            a single department's roster, or the employed set). ``None``
            counts every busy agent.
        running: Agent ids holding a live agent-state row, from
            :meth:`AgentStateRepository.get_active`. ``None`` reads the board
            alone, which is what a caller with no persistence can answer.

    Returns:
        The set of busy agent ids.
    """
    candidate_set = frozenset(candidates) if candidates is not None else None
    busy = {
        task.assigned_to
        for task in tasks
        if task.status == TaskStatus.IN_PROGRESS and task.assigned_to
    }
    if running is not None:
        busy |= set(running)
    if candidate_set is None:
        return busy
    return busy & candidate_set
