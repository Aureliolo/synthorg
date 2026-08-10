# module-kind: code
"""Retire the approvals that decide about a row, before the row is removed.

An approval is a question about something that exists. Once that something is
deleted the question is unanswerable, but the approval is not inert: the queue
still offers approve and reject, and answering drives the resume path against
an id that resolves to nothing.

Retirement runs BEFORE the delete and the delete is conditional on it. The
reverse order has no safe failure: once the row is gone, a retirement that does
not persist leaves an actionable approval pointing at nothing, and the only
remaining move is to log it, which reports that the window is open rather than
closing it. Retiring first inverts that into a delete the operator can simply
retry, and closes the window in between: an approval already EXPIRED cannot be
decided while the row is being removed.

Expired rather than rejected: a rejection is a reviewer's verdict, and nobody
made one. The approval simply has nothing left to decide.

A refused delete leaves the queue exactly as it found it, and that has to hold
for BOTH refusals. A decision landing between the read and the write is one:
the verdict was made while the row still existed, the resume path is acting on
it, and deleting underneath that is the race this module exists to prevent. The
delete failing on its own preconditions is the other, and it is the commoner
one: ``delete_task`` refuses a task a plan still names as its objective, which
an operator can provoke deliberately. Retirement is therefore scoped to the
delete rather than merely sequenced before it, so a delete that raises puts
every approval this call expired back. Without that, a delete anyone can get
refused strips a task's pending reviews for good.

A project has no approval of its own. Its plans and tasks do, and they are
retired together in one pass, because the pending set is read whole and reading
it once per child turns a cascade into a scan per row.
"""

import contextlib
from collections.abc import AsyncIterator, Callable, Collection

from synthorg.api.lifecycle_helpers.plan_questions import PLAN_ID_METADATA_KEY
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalSource, ApprovalStatus
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import ConflictError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_APPROVAL_RESTORED,
    API_APPROVAL_RETIRED,
)

logger = get_logger(__name__)


def retiring_plan_approvals(
    app_state: AppState,
    plan_id: str,
) -> contextlib.AbstractAsyncContextManager[None]:
    """Expire the pending review approval that names *plan_id*, for a delete.

    Matched on the approval's own ``plan_id`` metadata rather than a filter
    field, because that metadata is the link: the store has no plan filter,
    and the pending set is bounded by the review inbox an operator works
    through rather than by the plan table.

    Args:
        app_state: Application state carrying the approval store.
        plan_id: The plan about to be deleted.

    Returns:
        A context manager whose body performs the delete. Leaving it with an
        exception restores every approval it expired.
    """
    return _retire_around_delete(
        app_state,
        subject=f"plan {plan_id}",
        matches=lambda item: (
            item.source is ApprovalSource.PLAN_REVIEW
            and item.metadata.get(PLAN_ID_METADATA_KEY) == plan_id
        ),
    )


def retiring_task_approvals(
    app_state: AppState,
    task_id: str,
) -> contextlib.AbstractAsyncContextManager[None]:
    """Expire every pending approval raised against *task_id*, for a delete.

    Every source, not just the review gate: a task carries whatever was asked
    about it, and which sources those are is a property of how the run went
    rather than something a deletion can know.

    Args:
        app_state: Application state carrying the approval store.
        task_id: The task about to be deleted.

    Returns:
        A context manager whose body performs the delete.
    """
    return retiring_approvals_for_tasks(app_state, (task_id,))


def retiring_approvals_for_tasks(
    app_state: AppState,
    task_ids: Collection[str],
) -> contextlib.AbstractAsyncContextManager[None]:
    """Expire every pending approval raised against any of *task_ids*.

    One pass for the whole set. The store answers "every pending approval"
    and nothing narrower, so asking once per task turns a cascade over a
    project's tasks into a scan of the queue per row.

    Args:
        app_state: Application state carrying the approval store.
        task_ids: The tasks about to be deleted.

    Returns:
        A context manager whose body performs the delete.
    """
    wanted = frozenset(task_ids)
    if not wanted:
        # A cascade page can come back empty, and scanning the queue to match
        # nothing would still cost the scan.
        return contextlib.nullcontext()
    subject = f"task {next(iter(wanted))}" if len(wanted) == 1 else "these tasks"
    return _retire_around_delete(
        app_state,
        subject=subject,
        matches=lambda item: item.task_id in wanted,
    )


@contextlib.asynccontextmanager
async def _retire_around_delete(
    app_state: AppState,
    *,
    subject: str,
    matches: Callable[[ApprovalItem], bool],
) -> AsyncIterator[None]:
    """Expire every approval *matches* selects, and undo that if the body fails.

    The store is never handed to a helper: everything that touches it lives in
    this one function, so a partially-built double in a test is not checked
    against the whole store protocol on the way in.

    Args:
        app_state: Application state carrying the approval store.
        subject: What is being deleted, for the refusal message and the log.
        matches: Whether an approval decides about the row being removed.

    Yields:
        Once, with the matched approvals expired, for the caller to delete in.

    Raises:
        ConflictError: One matched approval could not be expired because it
            is no longer pending: either a reviewer decided it while the row
            still existed and the resume path is acting on that verdict, or a
            concurrent write is mid-flight. Either way the delete is refused
            rather than racing, anything already expired here is put back,
            and the operator retries once the dispatch has settled.
    """
    store = app_state.slice(ApprovalStateSlice).store
    if store is None:
        yield
        return
    pending = [
        item
        for item in await store.list_items(status=ApprovalStatus.PENDING)
        if matches(item)
    ]
    expired: list[ApprovalItem] = []
    for item in pending:
        retired = await store.save_if_pending(
            item.model_copy(update={"status": ApprovalStatus.EXPIRED})
        )
        if retired is None:
            # A refusal is not a verdict on its own: the row may already be
            # gone (a concurrent delete of the same subject did this work,
            # which satisfies rather than blocks this one) or already
            # expired for the same reason.
            current = await store.get(NotBlankStr(str(item.id)))
            if current is None or current.status is ApprovalStatus.EXPIRED:
                continue
            # Unconditional: these were pending moments ago and were expired
            # by this call, so nothing else has decided them, and a
            # conditional write would refuse on the status this call wrote.
            for restored in expired:
                await store.save(restored)
            msg = (
                f"{subject} has an approval that is no longer pending; nothing "
                "was deleted. Retry once the decision has been dispatched."
            )
            raise ConflictError(msg)
        expired.append(item)
        logger.info(
            API_APPROVAL_RETIRED,
            approval_id=str(item.id),
            subject=subject,
            source=item.source.value,
        )
    try:
        yield
    except Exception:
        # The delete did not happen, so neither did the retirement. Same
        # unconditional write as above, for the same reason: this call is the
        # only thing that has touched these rows since it read them.
        for restored in expired:
            await store.save(restored)
            logger.info(
                API_APPROVAL_RESTORED,
                approval_id=str(restored.id),
                subject=subject,
            )
        raise


__all__ = [
    "retiring_approvals_for_tasks",
    "retiring_plan_approvals",
    "retiring_task_approvals",
]
