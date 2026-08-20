# module-kind: code
"""Remove one task, with everything a deletion owes done in the right order.

Deleting a task is four steps that only work in one arrangement, and two
surfaces ask for it: the REST route and the ``synthorg_tasks_delete`` MCP
tool. Read the title first, because after the row is gone nothing can say what
the surviving cost, metric and decision rows are naming. Scope the approval
retirement to the delete, because a pending approval about a removed task is
still decidable and a refused delete must not take the task's reviews with it.
Delete. Then file the tombstone.

Written once because the arrangement is the substance. Two copies drift a step
at a time, and each step that goes missing fails quietly: an unread title
leaves a tombstone that cannot say what the id was, an unscoped retirement
strips reviews from a task that is still there, and an unwritten tombstone
turns every surviving reference into a dangling id.
"""

from synthorg.api.controllers._approval_retire import retiring_task_approvals
from synthorg.api.controllers._bulk_delete import BulkDeleteResult, run_bulk_delete
from synthorg.api.controllers._deletion_record import record_deletion_for
from synthorg.api.state import AppState
from synthorg.core.deleted_entity import DeletedEntityKind
from synthorg.core.types import NotBlankStr
from synthorg.engine.state import task_engine_of
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_TASK_DELETED

logger = get_logger(__name__)


async def remove_task(
    app_state: AppState,
    task_id: str,
    *,
    requested_by: str,
) -> None:
    """Delete *task_id*, retiring its approvals and recording what it was.

    Args:
        app_state: Application state carrying the task engine and stores.
        task_id: The task to remove.
        requested_by: The person who asked. Never a system actor: nothing in
            the system deletes a task on its own.

    Raises:
        TaskNotFoundError: No such task.
        ConflictError: An approval about this task was decided while the
            delete was being prepared, so it is still being acted on and
            nothing is removed.
    """
    engine = task_engine_of(app_state)
    existing = await engine.get_task(task_id)
    async with retiring_task_approvals(app_state, task_id) as retirement:
        await engine.delete_task(task_id, requested_by=requested_by)
        retirement.removed(task_id)
    await record_deletion_for(
        app_state,
        kind=DeletedEntityKind.TASK,
        entity_id=task_id,
        display_name=existing.title if existing is not None else None,
        deleted_by=requested_by,
    )


async def remove_tasks(
    app_state: AppState,
    ids: tuple[NotBlankStr, ...],
    *,
    requested_by: str,
) -> BulkDeleteResult:
    """Delete every task in *ids*, collecting the ones that refuse.

    A task a plan still names as its objective refuses, and clearing a board is
    the selection that mixes those in, so one refusal must not end the action.

    Args:
        app_state: Application state carrying the task engine and stores.
        ids: The tasks the operator selected.
        requested_by: The person who asked.

    Returns:
        What was removed and what remains.
    """

    async def _delete_one(task_id: str) -> None:
        await remove_task(app_state, task_id, requested_by=requested_by)
        logger.info(API_TASK_DELETED, task_id=task_id)

    return await run_bulk_delete(ids, _delete_one, entity="task")


__all__ = ["remove_task", "remove_tasks"]
