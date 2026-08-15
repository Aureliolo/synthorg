"""Decision core behind the two settling approval MCP tools.

``synthorg_approvals_approve`` and ``synthorg_approvals_reject`` differ only
in the status they write and the audit event they chain; everything between
"an actor asked" and "the run is moving again" is here: refusing what this
door will not decide, the compare-and-set against a still-PENDING item, and
waking whatever the decision unblocks.

What this door will not decide
------------------------------
Two classes are refused outright, both because the actor here is an agent: a
parked agent QUESTION (the org asking a person something), and an approval
that changes who is in the organisation. Everything else it does decide is
carried through ``signal_resume_intent``, because a decision written without
waking what it unblocks leaves the run stranded with nothing PENDING for any
other door to finish.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from synthorg.api.controllers._approval_review_gate import signal_resume_intent
from synthorg.api.resume_intent_outbox import (
    clear_resume_intent,
    record_resume_intent,
)
from synthorg.approval.enums import ApprovalStatus
from synthorg.approval.questions import is_question
from synthorg.approval.state import approval_store_of
from synthorg.core.agent import AgentIdentity
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.meta.mcp.handlers.common_args import require_actor_id
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_RESUME_FAILED,
)
from synthorg.security.autonomy.enums import ActionType

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


class _NotFoundError(
    LookupError,
):  # lint-allow: domain-error-hierarchy -- MCP handler-local; no HTTP layer
    """Handler-local not-found signal.

    Raised inside the try block so the ``err()`` envelope picks up
    ``domain_code=not_found`` without taking a dependency on Litestar's
    ``NotFoundError`` (that one would trigger 404 handling in HTTP
    paths; MCP has no HTTP layer).
    """

    domain_code = "not_found"


class _ConflictError(
    RuntimeError,
):  # lint-allow: domain-error-hierarchy -- MCP handler-local; no HTTP layer
    """Handler-local conflict signal (approve/reject race)."""

    domain_code = "conflict"


class _OutOfScopeError(
    RuntimeError,
):  # lint-allow: domain-error-hierarchy -- MCP handler-local; no HTTP layer
    """The approval exists but this door will not decide it."""

    domain_code = "forbidden"


#: Action types whose approval changes who exists in the organisation. A hire
#: mints a durable principal that outlives the decision, holds a role, spends
#: budget and can be selected to judge other agents' work; a firing removes
#: one. Membership is the operator's call, and an agent settling it here would
#: be an agent choosing the org's members through a queue meant for unblocking
#: its own run.
_PRINCIPAL_ACTION_TYPES: Final[frozenset[str]] = frozenset(
    {ActionType.ORG_HIRE.value, ActionType.ORG_FIRE.value}
)


def _refuse_principal_change(item: ApprovalItem) -> None:
    """Refuse an approval that adds or removes an organisational principal.

    Raises:
        _OutOfScopeError: When the approval changes org membership.
    """
    if item.action_type in _PRINCIPAL_ACTION_TYPES:
        msg = (
            f"Approval {item.id!s} changes who is in the organisation "
            f"({item.action_type}) and cannot be decided through the MCP "
            f"surface. Decide it in the approvals queue."
        )
        raise _OutOfScopeError(msg)


def _refuse_question(item: ApprovalItem) -> None:
    """Refuse an approval that is a human's question to answer.

    A parked question is the org asking a PERSON something it judged too
    material to settle alone. The actor on this door is an agent, so letting
    it answer turns the one deliberate human checkpoint into an agent-to-agent
    instruction channel, and writes an audit row saying a decision was made
    that nobody made.

    Raises:
        _OutOfScopeError: When the approval is a parked agent question.
    """
    if is_question(item.action_type):
        msg = (
            f"Approval {item.id!s} is a question awaiting a human answer and "
            f"cannot be decided through the MCP surface. Answer it in the "
            f"conversation or in the approvals queue."
        )
        raise _OutOfScopeError(msg)


async def _settle(
    app_state: AppState,
    saved: ApprovalItem,
    pending_snapshot: ApprovalItem,
) -> None:
    """Wake whatever the decision unblocks, or put the approval back.

    Writing the decision is only half of deciding: a parked run has to be
    resumed and a task in review has to be transitioned, and until that
    happens the approval is no longer PENDING for anyone else to finish. So
    this door routes through the same internal entrypoint the dashboard and
    the inbound-chat dispatcher use rather than stopping at the write.

    On failure the approval is restored to PENDING and the marker cleared, so
    the decision is immediately retryable instead of stranded decided.

    Raises:
        Exception: Re-raised unchanged once the approval is back to PENDING,
            so the caller reports a failure rather than a false success.
    """
    approval_id = str(saved.id)
    try:
        await signal_resume_intent(
            app_state,
            approval_id,
            approved=saved.status is ApprovalStatus.APPROVED,
            decided_by=saved.decided_by,
            decision_reason=saved.decision_reason,
            task_id=saved.task_id,
        )
    except Exception as exc:
        reraise_critical(exc)
        await approval_store_of(app_state).save(pending_snapshot)
        await clear_resume_intent(app_state, approval_id)
        logger.warning(
            APPROVAL_GATE_RESUME_FAILED,
            approval_id=approval_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="mcp decision rolled back to pending",
        )
        raise
    await clear_resume_intent(app_state, approval_id)


async def _decide(
    *,
    app_state: AppState,
    approval_id: str,
    actor: AgentIdentity | None,
    target: ApprovalStatus,
    reason: str | None,
) -> ApprovalItem:
    """Shared approve/reject finalisation.

    Fetches the current item, stamps decision fields, and writes via
    ``save_if_pending`` so a concurrent decision cannot race us past
    first-writer-wins.  When ``save_if_pending`` returns ``None`` we
    re-read the approval to distinguish *gone* (``_NotFoundError``) from
    *raced to a new state* (``_ConflictError``) -- a silent collapse to
    "conflict" misleads callers when the item was actually deleted or
    expired between the fetch and the write.

    Raises:
        _NotFoundError: Approval id does not exist or was removed.
        _ConflictError: Item already decided or in-flight save.
        _OutOfScopeError: The approval is a question for a human.
        ArgumentValidationError: Actor is missing a decidable name.

    Returns:
        ``ApprovalItem`` instance.
    """
    decided_by = require_actor_id(actor)
    store = approval_store_of(app_state)
    existing = await store.get(approval_id)
    if existing is None:
        msg = f"Approval {approval_id!r} not found"
        raise _NotFoundError(msg)
    _refuse_question(existing)
    _refuse_principal_change(existing)
    if existing.status != ApprovalStatus.PENDING:
        msg = f"Approval {approval_id!r} is {existing.status.value!s}, not pending"
        raise _ConflictError(msg)
    # Recorded BEFORE the decision write, and ``decided_at`` stamped after it,
    # so the startup drain reads this marker as bracketing the decision rather
    # than postdating it. Same ordering as the other two decision doors.
    await record_resume_intent(app_state, approval_id)
    updated = existing.model_copy(
        update={
            "status": target,
            "decided_at": datetime.now(UTC),
            "decided_by": decided_by,
            "decision_reason": reason,
        },
    )
    saved: ApprovalItem | None = await store.save_if_pending(
        updated,
    )
    if saved is None:
        # The marker is left alone: a concurrent winner may own an in-flight
        # resume behind it, and clearing here would delete that safety net.
        current = await store.get(approval_id)
        if current is None:
            msg = f"Approval {approval_id!r} was removed before decision"
            raise _NotFoundError(msg)
        msg = (
            f"Approval {approval_id!r} was decided concurrently "
            f"(now {current.status.value!s})"
        )
        raise _ConflictError(msg)
    await _settle(app_state, saved, existing)
    return saved
