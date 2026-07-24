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
    """

    __slots__ = ("_config_resolver", "_dispatcher", "_registry")

    def __init__(
        self,
        *,
        registry: InboundThreadRegistry,
        dispatcher: ChatResumeDispatcher,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        self._registry = registry
        self._dispatcher = dispatcher
        self._config_resolver = config_resolver

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
        # Authorisation is checked AFTER the decision is resolved but BEFORE
        # any write: the thread correlation proves which approval an event
        # answers, never that this human may answer it. Anyone able to add a
        # reaction in the channel would otherwise decide a governed action.
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
