"""Route a decoded inbound chat event back to its parked approval.

A threaded reply / reaction is resolved to the approval it answers via the
:class:`~synthorg.integrations.chat_api.inbound.registry.InboundThreadRegistry`,
then handed to a :class:`ChatResumeDispatcher` that records the decision
and resumes the parked task. Decision semantics:

- an approve/reject reaction (``white_check_mark`` / ``x`` ...) is an
  explicit decision;
- a text reply (mention / message / DM) is an approving reply whose body
  becomes the human guidance the resumed agent sees.

The router never feeds the raw human text to a prompt: it forwards it as
``decision_reason`` only, which the resume machinery fences with
``wrap_untrusted(TAG_TASK_DATA, ...)`` before it reaches any LLM boundary
(the same path the dashboard approval comment takes).
"""

from typing import Final, Protocol, runtime_checkable

from synthorg.integrations.chat_api.inbound.models import (
    InboundChatEvent,
    InboundEventKind,
)
from synthorg.integrations.chat_api.inbound.registry import InboundThreadRegistry
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    CHAT_INBOUND_EVENT_IGNORED,
    CHAT_INBOUND_EVENT_ROUTED,
)

logger = get_logger(__name__)

# Reaction shortcodes (no leading/trailing colons) that decide an approval.
_APPROVE_REACTIONS: Final[frozenset[str]] = frozenset(
    {"white_check_mark", "heavy_check_mark", "+1", "thumbsup", "ok_hand"},
)
_REJECT_REACTIONS: Final[frozenset[str]] = frozenset(
    {"x", "no_entry", "no_entry_sign", "-1", "thumbsdown"},
)


@runtime_checkable
class ChatResumeDispatcher(Protocol):
    """Records an approval decision and resumes the parked task."""

    async def resume(
        self,
        *,
        approval_id: str,
        approved: bool,
        decided_by: str,
        decision_reason: str,
    ) -> bool:
        """Apply the decision; return ``True`` iff it resumed a task."""
        ...


class _Decision:
    """A resolved (approved, reason) pair, or ``None`` to ignore."""

    __slots__ = ("approved", "reason")

    def __init__(self, *, approved: bool, reason: str) -> None:
        self.approved = approved
        self.reason = reason


def _decide(event: InboundChatEvent) -> _Decision | None:
    """Resolve an inbound event to an approve/reject decision.

    Returns:
        The decision, or ``None`` for an unrecognised reaction or an
        empty text reply (nothing to act on).
    """
    if event.kind is InboundEventKind.REACTION:
        if event.reaction in _APPROVE_REACTIONS:
            return _Decision(approved=True, reason="Approved via reaction")
        if event.reaction in _REJECT_REACTIONS:
            return _Decision(approved=False, reason="Rejected via reaction")
        return None
    # A text reply is an approving reply whose body is the human guidance.
    if not event.text.strip():
        return None
    return _Decision(approved=True, reason=event.text)


class InboundResumeRouter:
    """Resolve an inbound event to an approval and resume the task.

    Args:
        registry: Thread -> approval correlation populated at notify time.
        dispatcher: Records the decision and resumes the parked task.
    """

    __slots__ = ("_dispatcher", "_registry")

    def __init__(
        self,
        *,
        registry: InboundThreadRegistry,
        dispatcher: ChatResumeDispatcher,
    ) -> None:
        self._registry = registry
        self._dispatcher = dispatcher

    async def route(self, event: InboundChatEvent) -> None:
        """Resume the approval this event answers, or ignore it."""
        approval_id = self._registry.resolve(
            channel=event.channel, thread_ts=event.thread_ts
        )
        if approval_id is None:
            logger.debug(
                CHAT_INBOUND_EVENT_IGNORED,
                kind=event.kind.value,
                reason="no_tracked_approval",
            )
            return
        decision = _decide(event)
        if decision is None:
            logger.debug(
                CHAT_INBOUND_EVENT_IGNORED,
                kind=event.kind.value,
                reason="no_decision",
            )
            return
        resumed = await self._dispatcher.resume(
            approval_id=approval_id,
            approved=decision.approved,
            decided_by=event.user,
            # Forwarded ONLY as decision_reason; the resume machinery fences
            # it with wrap_untrusted(TAG_TASK_DATA, ...) before any prompt.
            decision_reason=decision.reason,
        )
        if resumed:
            self._registry.discard(channel=event.channel, thread_ts=event.thread_ts)
            logger.info(
                CHAT_INBOUND_EVENT_ROUTED,
                approval_id=approval_id,
                approved=decision.approved,
            )
        else:
            logger.debug(
                CHAT_INBOUND_EVENT_IGNORED,
                approval_id=approval_id,
                reason="not_resumable",
            )


__all__ = ["ChatResumeDispatcher", "InboundResumeRouter"]
