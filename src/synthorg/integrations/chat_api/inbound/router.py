"""Route a decoded inbound chat event back to its parked approval.

A threaded reply / reaction is resolved to the approval it answers via the
:class:`~synthorg.integrations.chat_api.inbound.registry.InboundThreadRegistry`,
then handed to a :class:`ChatResumeDispatcher` that records the decision
and resumes the parked task. Decision semantics are explicit-token-only:

- ONLY an approve/reject reaction (``white_check_mark`` / ``x`` ...)
  constitutes a decision. Approval is consent, so it must never be
  inferred from arbitrary human text.
- A plain text reply (mention / message / DM) is discarded outright: it
  resolves to no decision and carries nothing onward, so it never reaches
  the resumed agent. The only reasons forwarded are the two fixed strings
  below, both derived from the reaction, never from human prose.
- The reacting user must be a configured decider
  (``tools.chat_inbound_deciders``). Being able to react in a channel is
  not authorisation, so an unlisted user is ignored and an unset
  allowlist denies every inbound decision.

The router never feeds raw human text to a prompt: it forwards a
decision's reason as ``decision_reason`` only, which the resume machinery
fences with ``wrap_untrusted(TAG_TASK_DATA, ...)`` before it reaches any
LLM boundary (the same path the dashboard approval comment takes).
"""

import asyncio
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
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
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_DECIDERS_KEY: Final[str] = "chat_inbound_deciders"

# A decision reaction can reach the consumer before the notifier finishes
# registering the thread: the message is live in Slack while our postMessage
# response is still returning, so the ``(channel, ts) -> approval_id`` mapping
# is written a beat later. Re-resolve a few times before giving up so a fast
# reaction is not dropped. Only decision-bearing reactions pay this; ordinary
# channel reactions resolve to no decision and never reach the retry.
_RESOLVE_ATTEMPTS: Final[int] = 4
_RESOLVE_RETRY_SECONDS: Final[float] = 0.5

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
        config_resolver: Live settings resolver for the decider allowlist.
            ``None`` denies every decision (an inbound control surface must
            never authorise itself).
        clock: Clock seam for the resolve-retry backoff; tests inject a fake.
    """

    __slots__ = ("_clock", "_config_resolver", "_dispatcher", "_registry")

    def __init__(
        self,
        *,
        registry: InboundThreadRegistry,
        dispatcher: ChatResumeDispatcher,
        config_resolver: ConfigResolver | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._registry = registry
        self._dispatcher = dispatcher
        self._config_resolver = config_resolver
        self._clock: Clock = clock if clock is not None else SystemClock()

    def set_config_resolver(self, resolver: ConfigResolver) -> None:
        """Wire the live settings resolver (post-construction)."""
        self._config_resolver = resolver

    async def _authorised_deciders(self) -> frozenset[str]:
        """Resolve the decider allowlist, fail-closed to empty.

        Returns:
            The configured Slack user IDs, or an empty set when no resolver
            is wired or the read fails. Empty denies every decision.

        Raises:
            asyncio.CancelledError: If cancelled during the resolver read.
        """
        if self._config_resolver is None:
            return frozenset()
        try:
            raw = await self._config_resolver.get_str(
                SettingNamespace.TOOLS.value, _DECIDERS_KEY
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                CHAT_INBOUND_EVENT_IGNORED,
                reason="decider_resolver_failed_fail_closed",
                error_type=type(exc).__name__,
            )
            return frozenset()
        return frozenset(part.strip() for part in raw.split(",") if part.strip())

    async def _resolve_approval(self, event: InboundChatEvent) -> str | None:
        """Resolve the approval a decision answers, retrying the notify race.

        The notifier registers ``(channel, ts) -> approval_id`` a beat after
        the message goes live, so a fast reaction can arrive first. Re-resolve
        a few times before conceding it is genuinely untracked.

        Returns:
            The approval id, or ``None`` once the bounded retries are spent.
        """
        for attempt in range(_RESOLVE_ATTEMPTS):
            approval_id = self._registry.resolve(
                channel=event.channel, thread_ts=event.thread_ts
            )
            if approval_id is not None:
                return approval_id
            if attempt + 1 < _RESOLVE_ATTEMPTS:
                await self._clock.sleep(_RESOLVE_RETRY_SECONDS)
        return None

    async def route(self, event: InboundChatEvent) -> None:
        """Resume the approval this event answers, or ignore it."""
        # Decide first: only a real approve/reject reaction is worth the
        # resolve retry, so ordinary channel reactions never pay for it.
        decision = _decide(event)
        if decision is None:
            logger.debug(
                CHAT_INBOUND_EVENT_IGNORED,
                kind=event.kind.value,
                reason="no_decision",
            )
            return
        approval_id = await self._resolve_approval(event)
        if approval_id is None:
            logger.debug(
                CHAT_INBOUND_EVENT_IGNORED,
                kind=event.kind.value,
                reason="no_tracked_approval",
            )
            return
        # Correlation identifies the approval, not an authorised decider;
        # authorise before any write (see _authorised_deciders).
        if event.user not in await self._authorised_deciders():
            logger.warning(
                CHAT_INBOUND_EVENT_IGNORED,
                approval_id=approval_id,
                kind=event.kind.value,
                reason="decider_not_authorised",
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
