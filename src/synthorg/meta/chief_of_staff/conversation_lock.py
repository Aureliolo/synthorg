"""Per-conversation asyncio lock registry.

Serialises the whole turn pipeline for a single conversation so two
concurrent ``converse``-style calls cannot interleave their snapshots
of history nor commit turns the other side never saw. Shared by the
1:1 / routed proposer and the multi-agent group chat.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass


@dataclass(slots=True)
class _LockEntry:
    """A per-conversation lock paired with its holder-plus-waiter refcount.

    Holding the lock and its refcount in one record makes the
    entry-exists-iff-refcount-positive invariant structural: a single
    dict key carries both, so there is no second dict that a future edit
    could leave out of step.
    """

    lock: asyncio.Lock
    refcount: int


class ConversationLockRegistry:
    """Hands out one :class:`asyncio.Lock` per conversation id, evicting idle ones.

    Each per-conversation entry is reference-counted by its in-flight
    holder plus queued waiters; the entry is evicted once the last holder
    releases, so the registry does not grow unbounded over the process
    lifetime. A waiter increments the count before it queues on the lock,
    so eviction (count == 0) can only happen when there is no holder AND
    no queued waiter, and a concurrent caller arriving after eviction
    simply mints a fresh lock.

    No guard lock is needed: the refcount increment (with lazy entry
    creation) and the decrement-and-evict each run as a single block with
    no ``await`` inside, so under cooperative single-threaded asyncio they
    are atomic with respect to other ``hold`` calls. Keeping the only
    ``await`` (acquiring the per-conversation lock) outside those blocks
    also means a cancellation can never land mid-mutation and strand an
    entry at a non-zero refcount: the ``finally`` decrement has no
    suspension point.
    """

    __slots__ = ("_entries",)

    def __init__(self) -> None:
        self._entries: dict[str, _LockEntry] = {}

    @asynccontextmanager
    async def hold(self, conversation_id: str) -> AsyncIterator[None]:
        """Hold the per-conversation lock for the duration of the block.

        Reference-counts the entry so the registry self-prunes: the entry
        is removed once the last holder releases.

        Args:
            conversation_id: The conversation whose turn pipeline to
                serialise.

        Yields:
            ``None`` while the lock is held.
        """
        entry = self._entries.get(conversation_id)
        if entry is None:
            entry = _LockEntry(lock=asyncio.Lock(), refcount=0)
            self._entries[conversation_id] = entry
        entry.refcount += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.refcount -= 1
            if entry.refcount == 0:
                del self._entries[conversation_id]


__all__ = ["ConversationLockRegistry"]
