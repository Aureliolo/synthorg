# module-kind: code
"""Reading the backlog of tasks no agent could take.

A gate park names its role by its blocked reason, so the sweep knows what it
is waiting on before it reads a row. This park does not: the role is whatever
the plan item asked for, recorded on the task when routing failed to staff it.
So the backlog has to be grouped before it can be swept, and a row that names
no role has to be told apart from one that names a role nobody holds -- the
first cannot be offered a hire at all, and reporting them together would let a
stranded row hide inside a count that reads as "waiting on a hire".
"""

from synthorg.core.task import Task
from synthorg.core.task_enums import UNROUTABLE_ROLE_KEY, BlockedReason, TaskStatus
from synthorg.persistence.task_protocol import TaskFilterSpec, TaskRepository


async def unroutable_by_role(
    task_repo: TaskRepository,
    *,
    page_size: int,
    max_pages: int,
) -> tuple[dict[str, list[Task]], int]:
    """Group the unroutable backlog by the role each row is waiting on.

    Args:
        task_repo: Read side for the parked backlog.
        page_size: Rows per query.
        max_pages: Ceiling on pages per pass, so a growing backlog cannot
            hold the pass open.

    Returns:
        The parked tasks by role, and how many named no role at all.

    Raises:
        PersistenceError: If the backlog cannot be read. Deliberately not
            degraded to an empty result: a zero invented here would read as
            "nothing is parked", which is the answer that ends the sweep.
    """
    by_role: dict[str, list[Task]] = {}
    roleless = 0
    offset = 0
    for _ in range(max_pages):
        page = await task_repo.query(
            TaskFilterSpec(
                status=TaskStatus.BLOCKED,
                blocked_reason=BlockedReason.NO_CAPABLE_AGENT,
            ),
            limit=page_size,
            offset=offset,
        )
        for task in page:
            role = task.metadata.get(UNROUTABLE_ROLE_KEY)
            if isinstance(role, str) and role.strip():
                by_role.setdefault(role, []).append(task)
            else:
                roleless += 1
        if len(page) < page_size:
            break
        # This read does not mutate the set it is walking, so the window
        # advances by the page: the releases happen after every page is in
        # hand, unlike the gate sweep, which releases as it goes.
        offset += len(page)
    return by_role, roleless


__all__ = ["unroutable_by_role"]
