# module-kind: code
"""Which plans on this page are waiting on a decision from the operator.

A plan's status records what the organisation last did with it. It cannot say
that the initiative has stopped and needs a person, so an operator scanning the
board reads ``executing`` on a plan whose every item is dead, which is the
surface telling them work is in flight when none is.

The open approval already holds that fact, so it is resolved here and sent
beside the row, the same discipline :mod:`synthorg.api._read_names` applies to
every name: one read per response rather than one per row, and never a lookup
the browser has to make for a key the row does not carry.

Read rather than stored, because storing it would be a second owner of a fact
the approval defines: an operator answering the decision would have to be
followed by a write onto the plan, and any path that missed it would leave the
board waiting on a decision that was taken.
"""

from collections.abc import Iterable

from synthorg.api.dto_named_rows import PlanPendingDecision
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalStatus
from synthorg.approval.initiative_stall import (
    INITIATIVE_STALL_ACTION_TYPE,
    PLAN_ID_METADATA_KEY,
)
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_RESOURCE_NOT_FOUND

logger = get_logger(__name__)

#: The first line of a decision's description, which is what a row shows. The
#: whole description is the operator's briefing and belongs on the approval
#: itself; a row that carried all of it would be unreadable.
_MAX_REASON_CHARS: int = 240


def _reason_of(description: str) -> str:
    """Reduce a decision's description to the sentence a row can show.

    Returns:
        Its first sentence, bounded.
    """
    first = description.strip().split("\n", 1)[0].strip()
    if len(first) <= _MAX_REASON_CHARS:
        return first
    return f"{first[: _MAX_REASON_CHARS - 1].rstrip()}..."


async def pending_plan_decisions(
    app_state: AppState,
    plan_ids: Iterable[str],
) -> dict[str, PlanPendingDecision]:
    """Resolve which of *plan_ids* have a decision waiting on the operator.

    One store read per response, filtered by action type at the store, so a
    page costs the same whether it holds one plan or fifty.

    A read that fails leaves every row without its decision rather than
    failing the page: the plans are already complete without it, and a
    degraded approvals store must not cost the operator the board as well.

    Returns:
        Map of plan id to the decision waiting on it (plans with none are
        omitted).
    """
    store = app_state.slice(ApprovalStateSlice).store
    wanted = set(plan_ids)
    if store is None or not wanted:
        return {}
    try:
        pending = await store.list_items(
            status=ApprovalStatus.PENDING,
            action_type=NotBlankStr(INITIATIVE_STALL_ACTION_TYPE),
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- the page is already complete without the
        # decision; a degraded approvals store must not also cost the board.
        reraise_critical(exc)
        logger.warning(
            API_RESOURCE_NOT_FOUND,
            resource_type="ApprovalItem",
            operation="pending_plan_decisions",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return {}
    waiting: dict[str, PlanPendingDecision] = {}
    for item in pending:
        plan_id = item.metadata.get(PLAN_ID_METADATA_KEY, "")
        if plan_id not in wanted:
            continue
        waiting[plan_id] = PlanPendingDecision(
            approval_id=NotBlankStr(str(item.id)),
            action_type=item.action_type,
            title=item.title,
            reason=NotBlankStr(_reason_of(str(item.description))),
        )
    return waiting


__all__ = ["pending_plan_decisions"]
