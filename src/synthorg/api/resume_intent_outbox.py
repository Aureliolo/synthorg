# module-kind: service
"""Crash recovery for the two-write approval decision.

Deciding an approval is two writes that cannot be merged into one: the
decision lands on the :class:`~synthorg.core.approval.ApprovalItem`
(moving it off PENDING), and only then does
:func:`~synthorg.api.controllers._approval_review_gate.signal_resume_intent`
wake the parked task. If the process dies between them the task is
stranded forever -- nothing is PENDING any more, so neither a redelivered
chat event nor the dashboard can act on it.

Both decision paths (the dashboard endpoint and the inbound chat
dispatcher) bracket their decision with :func:`record_resume_intent` and
:func:`clear_resume_intent`, and :class:`ResumeIntentDrain` finishes at
startup whatever the previous process left in flight.

The marker deliberately carries no copy of the decision: the approval row
is the system of record, so the drain reads the outcome from there and a
losing concurrent decider's overwrite cannot make the drain resume with
the wrong answer.
"""

from datetime import UTC, datetime

from synthorg.api.controllers._approval_review_gate import signal_resume_intent
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalStatus
from synthorg.approval.state import approval_store_of
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.pagination import collect_all
from synthorg.core.resume_intent import ResumeIntent
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_RESUME_INTENT_CLEAR_FAILED,
    APPROVAL_GATE_RESUME_INTENT_DISCARDED,
    APPROVAL_GATE_RESUME_INTENT_DRAIN_COMPLETED,
    APPROVAL_GATE_RESUME_INTENT_DRAIN_STARTED,
    APPROVAL_GATE_RESUME_INTENT_RECORD_FAILED,
    APPROVAL_GATE_RESUME_INTENT_REDISPATCH_FAILED,
    APPROVAL_GATE_RESUME_INTENT_REDISPATCHED,
)
from synthorg.persistence.state import resume_intents_of

logger = get_logger(__name__)


async def record_resume_intent(app_state: AppState, approval_id: str) -> None:
    """Mark *approval_id*'s resume as in flight, before the decision write.

    Best-effort: a failure here leaves the decision path unchanged (the
    approval is still decided and the resume still dispatched in this
    process), it only forfeits crash recovery for this one decision. It
    is therefore logged rather than raised, so an outbox fault cannot
    turn a serviceable approval into a 500.
    """
    repo = resume_intents_of(app_state)
    if repo is None:
        return
    try:
        await repo.save(
            ResumeIntent(
                approval_id=NotBlankStr(approval_id),
                recorded_at=datetime.now(UTC),
            )
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            APPROVAL_GATE_RESUME_INTENT_RECORD_FAILED,
            approval_id=approval_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def clear_resume_intent(app_state: AppState, approval_id: str) -> None:
    """Clear *approval_id*'s marker once its resume is settled.

    Best-effort for the same reason as :func:`record_resume_intent`. A
    marker left behind costs one redundant, idempotent re-dispatch at the
    next startup, which is strictly preferable to failing the request.
    """
    repo = resume_intents_of(app_state)
    if repo is None:
        return
    try:
        await repo.delete(NotBlankStr(approval_id))
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            APPROVAL_GATE_RESUME_INTENT_CLEAR_FAILED,
            approval_id=approval_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


class ResumeIntentDrain:
    """Finishes the resumes a previous process died part-way through.

    Runs once at startup: every surviving marker is resolved against the
    approval's own persisted state, then cleared.

    Args:
        app_state: Application state carrying the approval store, the
            outbox, and the services the resume flow routes through.
    """

    __slots__ = ("_app_state",)

    def __init__(self, app_state: AppState) -> None:
        self._app_state = app_state

    async def drain(self) -> int:
        """Resolve every in-flight marker.

        Returns:
            The number of markers that were re-dispatched.
        """
        repo = resume_intents_of(self._app_state)
        if repo is None:
            return 0
        intents = await collect_all(
            lambda limit, offset: repo.list_items(limit=limit, offset=offset)
        )
        if not intents:
            return 0
        logger.info(APPROVAL_GATE_RESUME_INTENT_DRAIN_STARTED, pending=len(intents))
        redispatched = 0
        for intent in intents:
            if await self._resolve(intent):
                redispatched += 1
        logger.info(
            APPROVAL_GATE_RESUME_INTENT_DRAIN_COMPLETED,
            pending=len(intents),
            redispatched=redispatched,
        )
        return redispatched

    async def _resolve(self, intent: ResumeIntent) -> bool:
        """Re-dispatch or discard one marker.

        Returns:
            ``True`` iff the resume was re-dispatched.
        """
        approval_id = intent.approval_id
        item = await approval_store_of(self._app_state).get(approval_id)
        # No approval, or one still PENDING: the decision never landed, so
        # the approval is still decidable by a human and there is nothing
        # to finish. Dropping the marker here is what keeps a crash
        # *before* the decision write from resurrecting a stale resume.
        if item is None or item.status is ApprovalStatus.PENDING:
            logger.info(
                APPROVAL_GATE_RESUME_INTENT_DISCARDED,
                approval_id=approval_id,
                reason="unknown" if item is None else "still_pending",
            )
            await clear_resume_intent(self._app_state, approval_id)
            return False
        # A marker recorded AFTER the decision cannot be bracketing it: it
        # was written by a caller that went on to lose ``save_if_pending``
        # (a duplicate event, or a stale dashboard POST against an approval
        # decided long ago). Re-dispatching on it would re-run a resume
        # that already completed, so it is discarded instead.
        if item.decided_at is not None and item.decided_at < intent.recorded_at:
            logger.info(
                APPROVAL_GATE_RESUME_INTENT_DISCARDED,
                approval_id=approval_id,
                reason="recorded_after_decision",
            )
            await clear_resume_intent(self._app_state, approval_id)
            return False
        try:
            await signal_resume_intent(
                self._app_state,
                approval_id,
                approved=item.status is ApprovalStatus.APPROVED,
                decided_by=item.decided_by,
                decision_reason=item.decision_reason,
                task_id=item.task_id,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # The marker is deliberately KEPT: the parked task is still
            # unresumed, and dropping it here would hide that forever.
            # The next startup retries.
            logger.warning(
                APPROVAL_GATE_RESUME_INTENT_REDISPATCH_FAILED,
                approval_id=approval_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        logger.info(
            APPROVAL_GATE_RESUME_INTENT_REDISPATCHED,
            approval_id=approval_id,
            approved=item.status is ApprovalStatus.APPROVED,
        )
        await clear_resume_intent(self._app_state, approval_id)
        return True


__all__ = ["ResumeIntentDrain", "clear_resume_intent", "record_resume_intent"]
