# module-kind: code
"""A keyed ``asyncio.Lock`` map that self-evicts idle entries.

The single home for the "one lock per key, evicted when idle" pattern
that several services need to serialise per-entity read-modify-write
sequences without leaking a lock object per key for the lifetime of the
process.
"""

import asyncio
from collections.abc import AsyncIterator, Hashable
from contextlib import asynccontextmanager


class RefcountedLockMap[K: Hashable]:
    """Keyed ``asyncio.Lock`` map that self-evicts entries at zero references.

    Holds at most one lock per key, and only while at least one caller is
    inside or awaiting :meth:`acquire` for that key.  When the final holder
    releases, the entry is removed, so the map stays bounded across a
    long-lived process regardless of how many distinct keys are seen.

    The get-or-create plus refcount bookkeeping runs as a single stretch of
    synchronous code with no ``await`` inside it, so the event loop cannot
    interleave another coroutine partway through.  That atomicity is what a
    guard mutex would otherwise provide: without an ``await`` between the
    ``dict.get`` and the ``dict[key] = lock`` write, two callers racing on a
    fresh key cannot each install a *distinct* lock object (the classic
    ``dict.setdefault(k, Lock())`` identity bug).  Keeping the teardown
    synchronous also makes it cancellation-safe -- an awaited guard in the
    ``finally`` could be cancelled before the decrement ran, leaking the
    refcount and pinning the entry forever.
    """

    __slots__ = ("_locks", "_refcounts")

    def __init__(self) -> None:
        self._locks: dict[K, asyncio.Lock] = {}
        self._refcounts: dict[K, int] = {}

    @asynccontextmanager
    async def acquire(self, key: K) -> AsyncIterator[None]:
        """Hold the lock for ``key`` for the duration of the ``async with``.

        Args:
            key: The entity identifier to serialise on.

        Yields:
            Control while the per-key lock is held; concurrent callers for
            the same key block, callers for different keys proceed.
        """
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        self._refcounts[key] = self._refcounts.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            remaining = self._refcounts[key] - 1
            if remaining <= 0:
                del self._refcounts[key]
                del self._locks[key]
            else:
                self._refcounts[key] = remaining

    def __len__(self) -> int:
        """Return the number of keys with a live lock entry.

        Returns:
            The count of currently-referenced keys; zero when idle.
        """
        return len(self._locks)
