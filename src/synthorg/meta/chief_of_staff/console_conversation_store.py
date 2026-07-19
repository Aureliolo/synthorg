# module-kind: service
"""Process-local store of operator-console conversation contexts.

Keyed by ``conversation_id`` so a multi-turn CONFIGURE session keeps its memory
(the accumulated :class:`AgentContext` conversation) across turns. Process-local
and TTL-bounded rather than durable: a console conversation is short-lived and
losing it on a restart is acceptable (the operator restates), mirroring the
:class:`SecretCaptureService` handle store. This is what lets the console
continue a setup flow (the connection type/name it gathered on an earlier turn)
instead of starting cold each turn.
"""

from datetime import datetime, timedelta
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.engine.context import AgentContext
from synthorg.observability import get_logger
from synthorg.observability.events.chief_of_staff import COS_CONSOLE_CONTEXT_PURGED

logger = get_logger(__name__)

DEFAULT_CONSOLE_CONVERSATION_TTL_SECONDS: Final[int] = 3600


class ConsoleConversationStore:
    """Process-local, TTL-bounded store of console conversation contexts.

    Args:
        clock: Time source (injected in tests).
        ttl_seconds: Idle lifetime before a conversation context is swept.
    """

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        ttl_seconds: int = DEFAULT_CONSOLE_CONVERSATION_TTL_SECONDS,
    ) -> None:
        self._clock: Clock = clock or SystemClock()
        self._ttl_seconds = ttl_seconds
        # conversation_id -> (context, last-saved time). Synchronous access is
        # atomic under the event loop (no I/O), so no lock is needed.
        self._contexts: dict[str, tuple[AgentContext, datetime]] = {}

    def load(self, conversation_id: str) -> AgentContext | None:
        """Return the stored context for a conversation, or ``None``.

        A context past its idle TTL is treated as absent (and dropped), so a
        stale conversation restarts cold rather than resuming ancient state.

        Returns:
            The prior conversation context, or ``None`` when absent or expired.
        """
        entry = self._contexts.get(conversation_id)
        if entry is None:
            return None
        context, saved_at = entry
        if saved_at + timedelta(seconds=self._ttl_seconds) <= self._clock.now():
            del self._contexts[conversation_id]
            return None
        return context

    def save(self, conversation_id: str, context: AgentContext) -> None:
        """Persist the conversation's latest context for the next turn."""
        self._contexts[conversation_id] = (context, self._clock.now())

    def purge_expired(self) -> int:
        """Sweep idle conversation contexts past their TTL.

        Returns:
            The number of conversation contexts purged.
        """
        cutoff = self._clock.now() - timedelta(seconds=self._ttl_seconds)
        expired = tuple(
            conversation_id
            for conversation_id, (_context, saved_at) in self._contexts.items()
            if saved_at <= cutoff
        )
        for conversation_id in expired:
            del self._contexts[conversation_id]
        if expired:
            logger.info(COS_CONSOLE_CONTEXT_PURGED, count=len(expired))
        return len(expired)


__all__ = [
    "DEFAULT_CONSOLE_CONVERSATION_TTL_SECONDS",
    "ConsoleConversationStore",
]
