"""Route a decoded inbound chat event back to its parked approval.

A threaded reply / reaction is resolved to the approval it answers via the
:class:`~synthorg.integrations.chat_api.inbound.registry.InboundThreadRegistry`,
then handed to a :class:`ChatResumeDispatcher` that records the decision
and resumes the parked task. Decision semantics are explicit-token-only:

- ONLY an approve/reject reaction (``white_check_mark`` / ``x`` ...)
  constitutes a decision. Approval is consent, so it must never be
  inferred from arbitrary human text: a plain text reply (mention /
  message / DM) never approves or rejects, it merely carries guidance the
  resumed agent sees once a reaction has decided the outcome.
- A text reply on its own resolves to no decision and is ignored (there is
  nothing to act on without an explicit approve/reject signal).

The router never feeds the raw human text to a prompt: it forwards a
decision's reason as ``decision_reason`` only, which the resume machinery
fences with ``wrap_untrusted(TAG_TASK_DATA, ...)`` before it reaches any
LLM boundary (the same path the dashboard approval comment takes).
"""

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class _Decision:
    """A resolved (approved, reason) pair, or ``None`` to ignore."""

    approved: bool
    reason: str


def _decide(event: InboundChatEvent) -> _Decision | None:
    """Resolve an inbound event to an approve/reject decision.

    Only an explicit approve/reject reaction decides: consent is never
    inferred from human text, so a text reply resolves to ``None``.

    Returns:
        The decision, or ``None`` for a text reply or an unrecognised
        reaction (nothing to act on without an explicit signal).
    """
    if event.kind is not InboundEventKind.REACTION:
        return None
    if event.reaction in _APPROVE_REACTIONS:
        return _Decision(approved=True, reason="Approved via reaction")
    if event.reaction in _REJECT_REACTIONS:
        return _Decision(approved=False, reason="Rejected via reaction")
    return None


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
