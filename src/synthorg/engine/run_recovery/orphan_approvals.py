# module-kind: code
"""Close the approvals whose subject no longer exists.

An approval is a question about something. Delete-time retirement answers it
for the deletes that know to: a task removal, a plan removal, a project
cascade. That is edge-triggered, and it covers the paths that remembered.

A live run finished with every project, plan and task deleted and three
approvals still PENDING, still offering approve and reject, none of them
carrying an expiry. Lazy expiration cannot reach them either: it fires on
``expires_at``, and an approval without one is never reconsidered by anything.
Enterable, no exit, nothing watching, in the lane an operator is asked to act
in.

So the complement is level-triggered, on the same shape the rest of the system
uses: every pass re-asks the same question, and whatever left an approval
stranded, the next pass closes it. Scoped to ``task_id`` deliberately. It is a
first-class field on the item, so the question "does this subject still exist"
has a definite answer; a plan-review approval is reached through metadata and
through the one path a plan can leave by, which already retires it.

EXPIRED rather than REJECTED, for the reason the delete path gives: a rejection
is a reviewer's verdict, and nobody made one. There is simply nothing left to
decide.
"""

from synthorg.approval.enums import ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.approval import ApprovalItem
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APPROVAL_RETIRED
from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)


async def _live_task_ids(
    persistence: PersistenceBackend, task_ids: frozenset[str]
) -> frozenset[str]:
    """Return which of *task_ids* still name a row.

    Args:
        persistence: Reads the tasks.
        task_ids: The subjects named by pending approvals.

    Returns:
        The ids that still resolve.
    """
    live: set[str] = set()
    for task_id in task_ids:
        if await persistence.tasks.get(NotBlankStr(task_id)) is not None:
            live.add(task_id)
    return frozenset(live)


async def _expire(store: ApprovalStoreProtocol, item: ApprovalItem) -> bool:
    """Expire one orphaned approval, if it is still pending.

    Compare-and-set rather than a blind write: this pass is periodic and the
    operator is working the same queue, so a decision can land between the read
    and the write. Theirs wins.

    Args:
        store: The approval store.
        item: The pending approval whose subject has gone.

    Returns:
        Whether this call expired it.
    """
    written = await store.save_if_pending(
        item.model_copy(update={"status": ApprovalStatus.EXPIRED})
    )
    if written is None:
        return False
    logger.info(
        API_APPROVAL_RETIRED,
        approval_id=str(item.id),
        subject=f"task {item.task_id}",
        source=item.source.value,
        reason="subject_deleted",
    )
    return True


async def retire_orphaned_approvals(
    *,
    store: ApprovalStoreProtocol,
    persistence: PersistenceBackend,
) -> int:
    """Expire every pending approval whose task no longer exists.

    Idempotent: an approval this pass expired is no longer pending, so a later
    pass does not see it.

    Args:
        store: The approval store, read and written.
        persistence: Reads whether each named task still exists.

    Returns:
        How many approvals this pass closed.
    """
    pending = await store.list_items(status=ApprovalStatus.PENDING)
    subjects = frozenset(
        str(item.task_id) for item in pending if item.task_id is not None
    )
    if not subjects:
        return 0
    live = await _live_task_ids(persistence, subjects)
    orphaned = [
        item
        for item in pending
        if item.task_id is not None and str(item.task_id) not in live
    ]
    retired = 0
    for item in orphaned:
        retired += int(await _expire(store, item))
    return retired


__all__ = ["retire_orphaned_approvals"]
