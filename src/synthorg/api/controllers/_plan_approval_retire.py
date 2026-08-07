# module-kind: code
"""Retire the parked review approval when its plan is deleted.

``DELETE /plans/{id}`` removes the plan row. A ``PENDING_REVIEW`` plan also
has a parked ``PLAN_REVIEW`` approval carrying its id, and that approval
outlives the row: approving it afterwards drives ``try_plan_review_resume``
against a plan that no longer exists, which fails the parent task over a
decision about something already deleted. Expiring the approval in the same
operation is what stops a deletion leaving a live decision behind it.

Expired rather than rejected: a rejection is a reviewer's verdict on the
plan, and nobody made one. The approval simply has nothing left to decide.
"""

from synthorg.api.lifecycle_helpers.plan_review_wiring import PLAN_ID_METADATA_KEY
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalSource, ApprovalStatus
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan import Plan
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_PLAN_DELETED

logger = get_logger(__name__)


async def retire_review_approval(app_state: AppState, plan: Plan) -> None:
    """Expire the pending review approval that names *plan*, if there is one.

    Matched on the approval's own ``plan_id`` metadata rather than a filter
    field, because that metadata is the link: the store has no plan filter,
    and the pending set is bounded by the review inbox an operator works
    through rather than by the plan table.

    A failure here does not fail the delete. The plan is already gone, and
    raising would report the operator's own successful deletion as an error
    while leaving them no way to retry it; the stranded approval is logged
    instead, which is the state a caller can act on.

    Args:
        app_state: Application state carrying the approval store.
        plan: The plan just deleted.
    """
    store = app_state.slice(ApprovalStateSlice).store
    if store is None:
        return
    plan_id = str(plan.id)
    try:
        for item in await store.list_items(status=ApprovalStatus.PENDING):
            if item.source is not ApprovalSource.PLAN_REVIEW:
                continue
            if item.metadata.get(PLAN_ID_METADATA_KEY) != plan_id:
                continue
            # ``save_if_pending`` and not ``save``: a decision landing
            # between the read and this write is a real verdict on a plan
            # that still existed when it was made, and must not be
            # overwritten by a status derived from a stale read.
            await store.save_if_pending(
                item.model_copy(update={"status": ApprovalStatus.EXPIRED})
            )
            logger.info(
                API_PLAN_DELETED,
                plan_id=plan_id,
                approval_id=str(item.id),
                note="parked review approval expired with its plan",
            )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- the delete already succeeded; a stranded
        # approval is logged rather than turned into a failure the caller
        # cannot retry.
        reraise_critical(exc)
        logger.warning(
            API_PLAN_DELETED,
            plan_id=plan_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="plan deleted but its parked review approval was not retired",
        )


__all__ = ["retire_review_approval"]
