"""Per-key asyncio lock registry with weak-value eviction.

The brain write path serialises everything that touches one project (revision
assignment, git snapshot, index) through a single lock per project, and the
git writer serialises commits the same way. A plain ``dict`` of locks grows
without bound as project ids churn; a :class:`weakref.WeakValueDictionary`
evicts a lock once no caller holds it, so the registry stays bounded.
"""

import asyncio
import weakref


class PerKeyLockRegistry:
    """Hand out one :class:`asyncio.Lock` per key, evicting unused locks.

    Callers sharing a key serialise through the same lock. A lock with no live
    holder is garbage-collected, so the registry does not leak an entry per
    distinct key seen over the process lifetime. The guard makes get-or-create
    atomic, so two coroutines racing on a fresh key receive the *same* lock
    rather than two independent ones.
    """

    __slots__ = ("_guard", "_locks")

    def __init__(self) -> None:
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        self._guard = asyncio.Lock()

    async def acquire_for(self, key: str) -> asyncio.Lock:
        """Return the lock for *key*, creating it on first use.

        The caller is expected to immediately ``async with`` the returned lock;
        the strong reference it holds for the critical section keeps the weak
        entry alive while the lock is in use, so a concurrent caller on the same
        key observes the same lock.

        Returns:
            The shared lock guarding *key*.
        """
        async with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock


__all__ = ["PerKeyLockRegistry"]
