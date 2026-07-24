"""Concrete resume dispatcher binding inbound chat to the approval flow.

Implements the inbound router's ``ChatResumeDispatcher`` seam against the
real approval machinery: it records the decision on the ``ApprovalItem``
(atomically, only while still PENDING) and then hands off to
:func:`signal_resume_intent` -- the same internal entrypoint the dashboard
approve/reject endpoint uses after persisting its decision -- so the
parked task resumes through the existing routing (conversational intake,
agent invite, plan review, project decision, mid-execution park). Living
in the api layer keeps the ``integrations`` inbound package free of any
engine/approval import.
"""

from datetime import UTC, datetime

from synthorg.api.controllers._approval_review_gate import signal_resume_intent
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalStatus
from synthorg.approval.state import approval_store_of
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    CHAT_INBOUND_EVENT_ROUTED,
    CHAT_INBOUND_RESUME_FAILED,
)

logger = get_logger(__name__)

_DEFAULT_DECIDER: NotBlankStr = NotBlankStr("chat-inbound")


class ApprovalResumeDispatcher:
    """Records an inbound decision and resumes the parked task.

    Args:
        app_state: Application state carrying the approval store and the
            services :func:`signal_resume_intent` routes through.
    """

    __slots__ = ("_app_state",)

    def __init__(self, app_state: AppState) -> None:
        self._app_state = app_state

    async def resume(
        self,
        *,
        approval_id: str,
        approved: bool,
        decided_by: str,
        decision_reason: str,
    ) -> bool:
        """Record the decision and resume the parked task.

        Returns:
            ``True`` iff the approval was still pending and the decision
            was recorded (and the resume flow dispatched); ``False`` when
            the approval is unknown or already decided (idempotent).
        """
        store = approval_store_of(self._app_state)
        item = await store.get(NotBlankStr(approval_id))
        if item is None or item.status is not ApprovalStatus.PENDING:
            return False
        decider = (
            NotBlankStr(decided_by.strip()) if decided_by.strip() else _DEFAULT_DECIDER
        )
        status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        updated = item.model_copy(
            update={
                "status": status,
                "decided_at": datetime.now(UTC),
                "decided_by": decider,
                "decision_reason": NotBlankStr(decision_reason),
            },
        )
        # Atomic first-writer-wins: if a concurrent dashboard decision or a
        # second inbound event already moved it off PENDING, this returns
        # None and we do not double-resume.
        if await store.save_if_pending(updated) is None:
            return False
        try:
            await signal_resume_intent(
                self._app_state,
                approval_id,
                approved=approved,
                decided_by=decider,
                # Raw human text; signal_resume_intent -> build_resume_message
                # fences it with wrap_untrusted(TAG_TASK_DATA, ...) before any
                # LLM boundary (the same path the dashboard comment takes).
                decision_reason=decision_reason,
                task_id=item.task_id,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # The decision landed but the resume dispatch did not, which
            # would strand the parked task: the approval is no longer
            # PENDING, so neither a redelivered event nor the dashboard
            # could act on it. Restore it so it stays decidable, and report
            # failure so the router keeps the thread correlation for a
            # retry rather than discarding it.
            await store.save(item)
            logger.warning(
                CHAT_INBOUND_RESUME_FAILED,
                approval_id=approval_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        logger.info(
            CHAT_INBOUND_EVENT_ROUTED, approval_id=approval_id, approved=approved
        )
        return True


__all__ = ["ApprovalResumeDispatcher"]
