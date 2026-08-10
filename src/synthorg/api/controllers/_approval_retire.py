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

An exception is not the only way a delete fails to happen, so leaving the body
normally is not proof that it did. The body says what it removed, by id, and
whatever it does not name is restored on the way out. Two callers need that and
neither raises: the project teardown skips a plan whose live tasks refuse it,
and a page of tasks can lose the fifth after removing four, where restoring the
whole page would put four approvals back against rows that have gone. The
polarity is deliberate: a caller that forgets to name a removed subject
resurrects an approval that decides about nothing, which every delete test in
this module already asserts against.

A project has no approval of its own. Its plans and tasks do, and they are
retired together in one pass, because the pending set is read whole and reading
it once per child turns a cascade into a scan per row.
"""

import asyncio
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


class RetiredApprovals:
    """The subjects a delete has actually removed, collected as it goes.

    Yielded by every context manager here. Naming a subject is what keeps its
    approvals expired; anything unnamed when the body ends is put back, whether
    the body ended by raising or by simply declining to delete.
    """

    def __init__(self) -> None:
        self._removed: set[str] = set()

    def removed(self, *subject_ids: str) -> None:
        """Record that the row named by each of *subject_ids* is gone.

        Args:
            subject_ids: The ids this delete removed. Safe to repeat, and
                safe to call for an id the body has already named.
        """
        self._removed.update(subject_ids)

    def is_removed(self, subject_id: str) -> bool:
        """Whether the body reported *subject_id* as removed.

        Returns:
            ``True`` once :meth:`removed` has named it.
        """
        return subject_id in self._removed


def retiring_plan_approvals(
    app_state: AppState,
    plan_id: str,
) -> contextlib.AbstractAsyncContextManager[RetiredApprovals]:
    """Expire the pending review approval that names *plan_id*, for a delete.

    Matched on the approval's own ``plan_id`` metadata rather than a filter
    field, because that metadata is the link: the store has no plan filter,
    and the pending set is bounded by the review inbox an operator works
    through rather than by the plan table.

    Args:
        app_state: Application state carrying the approval store.
        plan_id: The plan about to be deleted.

    Returns:
        A context manager whose body performs the delete and calls
        ``removed(plan_id)`` once it has. Ending without that call, by
        raising or otherwise, restores every approval it expired.
    """
    return _retire_around_delete(
        app_state,
        subject=f"plan {plan_id}",
        matches=lambda item: (
            item.source is ApprovalSource.PLAN_REVIEW
            and item.metadata.get(PLAN_ID_METADATA_KEY) == plan_id
        ),
        subject_id=lambda _item: plan_id,
    )


def retiring_task_approvals(
    app_state: AppState,
    task_id: str,
) -> contextlib.AbstractAsyncContextManager[RetiredApprovals]:
    """Expire every pending approval raised against *task_id*, for a delete.

    Every source, not just the review gate: a task carries whatever was asked
    about it, and which sources those are is a property of how the run went
    rather than something a deletion can know.

    Args:
        app_state: Application state carrying the approval store.
        task_id: The task about to be deleted.

    Returns:
        A context manager whose body performs the delete and calls
        ``removed(task_id)`` once it has.
    """
    return retiring_approvals_for_tasks(app_state, (task_id,))


def retiring_approvals_for_tasks(
    app_state: AppState,
    task_ids: Collection[str],
) -> contextlib.AbstractAsyncContextManager[RetiredApprovals]:
    """Expire every pending approval raised against any of *task_ids*.

    One pass for the whole set. The store answers "every pending approval"
    and nothing narrower, so asking once per task turns a cascade over a
    project's tasks into a scan of the queue per row. Settlement stays per
    task even so: the body names each id as it removes it, so a page that
    fails partway restores only the tasks that are still there.

    Args:
        app_state: Application state carrying the approval store.
        task_ids: The tasks about to be deleted.

    Returns:
        A context manager whose body performs the deletes and names each
        removed task.
    """
    wanted = frozenset(task_ids)
    if not wanted:
        # A cascade page can come back empty, and scanning the queue to match
        # nothing would still cost the scan.
        return contextlib.nullcontext(RetiredApprovals())
    subject = f"task {next(iter(wanted))}" if len(wanted) == 1 else "these tasks"
    return _retire_around_delete(
        app_state,
        subject=subject,
        matches=lambda item: item.task_id in wanted,
        subject_id=lambda item: str(item.task_id),
    )


@contextlib.asynccontextmanager
async def _retire_around_delete(
    app_state: AppState,
    *,
    subject: str,
    matches: Callable[[ApprovalItem], bool],
    subject_id: Callable[[ApprovalItem], str],
) -> AsyncIterator[RetiredApprovals]:
    """Expire every approval *matches* selects, and undo that for what survives.

    The store is never handed to a helper: everything that touches it lives in
    this one function, so a partially-built double in a test is not checked
    against the whole store protocol on the way in.

    Args:
        app_state: Application state carrying the approval store.
        subject: What is being deleted, for the refusal message and the log.
        matches: Whether an approval decides about the row being removed.
        subject_id: The id of the row an approval decides about, which is what
            the body names once that row is gone.

    Yields:
        Once, with the matched approvals expired, the handle the body names
        each removed row on.

    Raises:
        ConflictError: One matched approval could not be expired because it
            is no longer pending: either a reviewer decided it while the row
            still existed and the resume path is acting on that verdict, or a
            concurrent write is mid-flight. Either way the delete is refused
            rather than racing, anything already expired here is put back,
            and the operator retries once the dispatch has settled.
        CancelledError: The body was cancelled mid-delete. Re-raised after the
            same restore any other failure gets, because a request abandoned
            here leaves the row present with its approvals expired.
    """
    store = app_state.slice(ApprovalStateSlice).store
    if store is None:
        yield RetiredApprovals()
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
    retirement = RetiredApprovals()

    async def _restore_survivors() -> None:
        """Put back every approval whose row the body did not remove.

        The same unconditional write as above, for the same reason: this call
        is the only thing that has touched these rows since it read them.
        """
        for item in expired:
            if retirement.is_removed(subject_id(item)):
                continue
            await store.save(item)
            logger.info(
                API_APPROVAL_RESTORED,
                approval_id=str(item.id),
                subject=subject,
            )

    try:
        yield retirement
    except Exception, asyncio.CancelledError:
        # The delete raised, so it did not happen and neither did the
        # retirement of anything the body had not already reported gone.
        #
        # Cancellation is listed because it is not an ``Exception``, and a
        # request abandoned mid-delete is the one failure mode that leaves the
        # row present with its approvals expired. Not ``BaseException``: a
        # ``SystemExit`` or a ``KeyboardInterrupt`` is the process ending, and
        # a store write on the way out is neither owed nor likely to land.
        await _restore_survivors()
        raise
    # Leaving normally is not proof the delete happened: a teardown skips a
    # plan whose live tasks refuse it, and reports nothing removed.
    await _restore_survivors()


__all__ = [
    "RetiredApprovals",
    "retiring_approvals_for_tasks",
    "retiring_plan_approvals",
    "retiring_task_approvals",
]
