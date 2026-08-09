# module-kind: code
"""Retire the parked review approval before its plan is deleted.

``DELETE /plans/{id}`` removes the plan row. A ``PENDING_REVIEW`` plan also
has a parked ``PLAN_REVIEW`` approval carrying its id, and that approval
outlives the row: approving it afterwards drives ``try_plan_review_resume``
against a plan that no longer exists, which fails the parent task over a
decision about something already deleted.

Retirement therefore runs BEFORE the delete and the delete is conditional on
it. The reverse order has no safe failure: once the row is gone, a retirement
that does not persist leaves an actionable approval pointing at nothing, and
the only remaining move is to log it, which is a report that the window is
open rather than a way to close it. Retiring first inverts that into a
delete the operator can simply retry, and closes the window in between: an
approval already EXPIRED cannot be decided while the row is being removed.

Expired rather than rejected: a rejection is a reviewer's verdict on the
plan, and nobody made one. The approval simply has nothing left to decide.

A decision that lands between the read and the write is NOT overwritten.
``save_if_pending`` refuses it, and that refusal aborts the delete: the
verdict was made while the plan still existed, the resume path is acting on
it, and deleting the plan underneath that is the race this module exists to
prevent.
"""

from synthorg.api.lifecycle_helpers.plan_questions import PLAN_ID_METADATA_KEY
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalSource, ApprovalStatus
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.domain_errors import ConflictError
from synthorg.core.plan import Plan
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_PLAN_DELETED

logger = get_logger(__name__)


async def retire_review_approval(app_state: AppState, plan: Plan) -> None:
    """Expire the pending review approval that names *plan*, if there is one.

    Matched on the approval's own ``plan_id`` metadata rather than a filter
    field, because that metadata is the link: the store has no plan filter,
    and the pending set is bounded by the review inbox an operator works
    through rather than by the plan table.

    Args:
        app_state: Application state carrying the approval store.
        plan: The plan about to be deleted.

    Raises:
        ConflictError: The approval was decided concurrently, so a reviewer's
            verdict is already being acted on against a plan that still
            existed when they made it. The delete is refused rather than
            racing the resume path; the operator retries once the dispatch
            has settled.
    """
    store = app_state.slice(ApprovalStateSlice).store
    if store is None:
        return
    plan_id = str(plan.id)
    for item in await store.list_items(status=ApprovalStatus.PENDING):
        if item.source is not ApprovalSource.PLAN_REVIEW:
            continue
        if item.metadata.get(PLAN_ID_METADATA_KEY) != plan_id:
            continue
        retired = await store.save_if_pending(
            item.model_copy(update={"status": ApprovalStatus.EXPIRED})
        )
        if retired is None:
            msg = (
                f"plan {plan_id} has a review approval that was decided while "
                "the delete was being prepared; the plan was not deleted. "
                "Retry once the decision has been dispatched."
            )
            raise ConflictError(msg)
        logger.info(
            API_PLAN_DELETED,
            plan_id=plan_id,
            approval_id=str(item.id),
            note="parked review approval expired ahead of its plan",
        )


__all__ = ["retire_review_approval"]
