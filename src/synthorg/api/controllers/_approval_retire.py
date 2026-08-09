# module-kind: code
"""Retire the approvals that decide about a row, before the row is removed.

An approval is a question about something that exists. Once that something is
deleted the question is unanswerable, but the approval is not inert: the queue
still offers approve and reject, and answering drives the resume path against
an id that resolves to nothing. A live run left four ``review:task_failed``
approvals PENDING against tasks the project teardown had already removed.

Retirement runs BEFORE the delete and the delete is conditional on it. The
reverse order has no safe failure: once the row is gone, a retirement that does
not persist leaves an actionable approval pointing at nothing, and the only
remaining move is to log it, which reports that the window is open rather than
closing it. Retiring first inverts that into a delete the operator can simply
retry, and closes the window in between: an approval already EXPIRED cannot be
decided while the row is being removed.

Expired rather than rejected: a rejection is a reviewer's verdict, and nobody
made one. The approval simply has nothing left to decide.

A decision that lands between the read and the write is NOT overwritten.
``save_if_pending`` refuses it, and that refusal aborts the delete: the verdict
was made while the row still existed, the resume path is acting on it, and
deleting underneath that is the race this module exists to prevent.

A project has no approval of its own. Its plans and tasks do, and each is
retired as that child is removed.
"""

from collections.abc import Callable

from synthorg.api.lifecycle_helpers.plan_questions import PLAN_ID_METADATA_KEY
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalSource, ApprovalStatus
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import ConflictError
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APPROVAL_RETIRED

logger = get_logger(__name__)


async def retire_plan_approvals(app_state: AppState, plan_id: str) -> None:
    """Expire the pending review approval that names *plan_id*.

    Matched on the approval's own ``plan_id`` metadata rather than a filter
    field, because that metadata is the link: the store has no plan filter,
    and the pending set is bounded by the review inbox an operator works
    through rather than by the plan table.

    Args:
        app_state: Application state carrying the approval store.
        plan_id: The plan about to be deleted.

    Raises:
        ConflictError: The approval was decided concurrently.
    """
    await _expire_matching(
        app_state,
        subject=f"plan {plan_id}",
        matches=lambda item: (
            item.source is ApprovalSource.PLAN_REVIEW
            and item.metadata.get(PLAN_ID_METADATA_KEY) == plan_id
        ),
    )


async def retire_task_approvals(app_state: AppState, task_id: str) -> None:
    """Expire every pending approval raised against *task_id*.

    Every source, not just the review gate: a task carries whatever was asked
    about it, and which sources those are is a property of how the run went
    rather than something a deletion can know.

    Args:
        app_state: Application state carrying the approval store.
        task_id: The task about to be deleted.

    Raises:
        ConflictError: An approval was decided concurrently.
    """
    await _expire_matching(
        app_state,
        subject=f"task {task_id}",
        matches=lambda item: item.task_id == task_id,
    )


async def _expire_matching(
    app_state: AppState,
    *,
    subject: str,
    matches: Callable[[ApprovalItem], bool],
) -> None:
    """Expire every pending approval *matches* selects.

    Args:
        app_state: Application state carrying the approval store.
        subject: What is being deleted, for the refusal message and the log.
        matches: Whether an approval decides about the row being removed.

    Raises:
        ConflictError: One matched approval was decided between the read and
            the write, so a reviewer's verdict is already being acted on
            against a row that still existed when they made it. The delete is
            refused rather than racing the resume path; the operator retries
            once the dispatch has settled.
    """
    store = app_state.slice(ApprovalStateSlice).store
    if store is None:
        return
    for item in await store.list_items(status=ApprovalStatus.PENDING):
        if not matches(item):
            continue
        retired = await store.save_if_pending(
            item.model_copy(update={"status": ApprovalStatus.EXPIRED})
        )
        if retired is None:
            msg = (
                f"{subject} has an approval that was decided while the delete "
                "was being prepared; nothing was deleted. Retry once the "
                "decision has been dispatched."
            )
            raise ConflictError(msg)
        logger.info(
            API_APPROVAL_RETIRED,
            approval_id=str(item.id),
            subject=subject,
            source=item.source.value,
        )


__all__ = ["retire_plan_approvals", "retire_task_approvals"]
