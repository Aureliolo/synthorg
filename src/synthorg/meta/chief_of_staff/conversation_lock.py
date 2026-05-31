"""Per-conversation asyncio lock registry.

Serialises the whole turn pipeline for a single conversation so two
concurrent ``converse``-style calls cannot interleave their snapshots
of history nor commit turns the other side never saw. Shared by the
1:1 / routed proposer and the multi-agent group chat.
"""

import asyncio


class ConversationLockRegistry:
    """Hands out one :class:`asyncio.Lock` per conversation id.

    The guard lock is lazy-initialised on first use so it binds to the
    live request-handling event loop rather than whichever loop built
    the registry. Locks accumulate over the process lifetime: the dict
    cannot be safely pruned without a refcount of in-flight + queued
    waiters (a deletion otherwise lets a concurrent caller mint a fresh
    lock while a queued waiter on the old one is still pending), which
    the conversational surfaces do not need at their expected scale.
    """

    __slots__ = ("_guard", "_locks")

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard: asyncio.Lock | None = None

    async def acquire_for(self, conversation_id: str) -> asyncio.Lock:
        """Return the lock for *conversation_id*, creating it once.

        A tight race on first guard-init merely wastes a ``Lock()``
        instance (the loser drops its instance after observing the
        populated guard).

        Returns:
            The shared :class:`asyncio.Lock` for *conversation_id*.
        """
        if self._guard is None:
            self._guard = asyncio.Lock()
        async with self._guard:
            lock = self._locks.get(conversation_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[conversation_id] = lock
            return lock


__all__ = ["ConversationLockRegistry"]
