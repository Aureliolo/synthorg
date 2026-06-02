"""Per-conversation asyncio lock registry.

Serialises the whole turn pipeline for a single conversation so two
concurrent ``converse``-style calls cannot interleave their snapshots
of history nor commit turns the other side never saw. Shared by the
1:1 / routed proposer and the multi-agent group chat.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class ConversationLockRegistry:
    """Hands out one :class:`asyncio.Lock` per conversation id, evicting idle ones.

    The guard lock is lazy-initialised on first use so it binds to the
    live request-handling event loop rather than whichever loop built
    the registry. Each per-conversation lock is reference-counted by its
    in-flight holder plus queued waiters; the entry is evicted once the
    last holder releases, so the registry does not grow unbounded over
    the process lifetime. A waiter increments the count before it queues
    on the lock, so eviction (count == 0) can only happen when there is
    no holder AND no queued waiter, and a concurrent caller arriving
    after eviction simply mints a fresh lock.
    """

    __slots__ = ("_guard", "_locks", "_refcounts")

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._refcounts: dict[str, int] = {}
        self._guard: asyncio.Lock | None = None

    def _ensure_guard(self) -> asyncio.Lock:
        """Return the guard lock, creating it on the live loop on first use.

        Safe without its own lock: there is no ``await`` between the
        ``None`` check and the assignment, so a coroutine is never
        preempted mid-init.

        Returns:
            The registry's guard :class:`asyncio.Lock`.
        """
        if self._guard is None:
            self._guard = asyncio.Lock()
        return self._guard

    @asynccontextmanager
    async def hold(self, conversation_id: str) -> AsyncIterator[None]:
        """Hold the per-conversation lock for the duration of the block.

        Reference-counts the lock so the registry self-prunes: the entry
        is removed once the last holder releases.

        Args:
            conversation_id: The conversation whose turn pipeline to
                serialise.

        Yields:
            ``None`` while the lock is held.
        """
        guard = self._ensure_guard()
        async with guard:
            lock = self._locks.get(conversation_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[conversation_id] = lock
                self._refcounts[conversation_id] = 0
            self._refcounts[conversation_id] += 1
        try:
            async with lock:
                yield
        finally:
            async with guard:
                self._refcounts[conversation_id] -= 1
                if self._refcounts[conversation_id] == 0:
                    del self._locks[conversation_id]
                    del self._refcounts[conversation_id]


__all__ = ["ConversationLockRegistry"]
