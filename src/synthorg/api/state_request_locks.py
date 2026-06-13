"""Per-request lifecycle-lock registry.

Owns the cross-cutting mutable primitive a frozen feature slice cannot
hold: the per-request lifecycle-lock registry with its bounded-size
eviction sweep. Composed onto ``AppState`` as ``app_state.request_locks``;
the request stores and auth-service themselves live on ``ApiCoreStateSlice``.
"""

import asyncio
import threading
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    REQUEST_LOCK_EVICTED_AT_CAP,
    REQUEST_LOCK_RELEASE_SKIPPED_WHILE_HELD,
)

logger = get_logger(__name__)

# Defence-in-depth cap on the per-AppState request-lock registry.
# ``scope_request`` retains the lock across handler exit (the next
# approve/reject for the same id needs it), so an authenticated
# client that scopes unique ids and never advances them would
# otherwise grow the dict forever. 10k is well above any realistic
# in-flight working set for a single org.
_MAX_REQUEST_LOCKS: int = 10_000


class RequestLockRegistry:
    """Per-request lifecycle-lock registry with bounded eviction.

    Serialises ``scope`` / ``approve`` / ``reject`` transitions on a
    request id. The registry dict is guarded by a plain ``threading.Lock``
    (not an ``asyncio.Lock``) because ``get_or_create`` runs in a
    synchronous context: the "check, then create" must serialise itself
    thread-safely without requiring a running event loop.
    """

    __slots__ = ("_guard", "_locks", "_refs")

    def __init__(self) -> None:
        """Build an empty, refcounted lock registry."""
        # Per-request-id lifecycle-lock registry (bounded, refcounted).
        self._locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._guard: threading.Lock = threading.Lock()
        self._refs: dict[str, int] = {}

    def get_or_create(self, request_id: str) -> asyncio.Lock:
        """Return the per-request lifecycle lock, creating it if absent.

        Low-level primitive that exposes the cached Lock for tests and
        diagnostics. Production callers MUST go through :meth:`acquire`
        instead, which pairs this with a refcount bump so a concurrent
        eviction sweep cannot drop the entry between receiving the Lock
        and entering ``async with``.

        On insert, the registry is capped at ``_MAX_REQUEST_LOCKS``: if
        adding the new entry would exceed the cap, the oldest **idle**
        entries are evicted (still-held or in-flight locks are kept so
        an in-flight approve/reject never strands a waiter on an
        evicted Lock). The cap defends against an authenticated client
        that scopes unique ids and never advances them to a terminal
        state, which would otherwise grow the dict without bound.

        Returns:
            ``asyncio.Lock`` instance.
        """
        lock = self._locks.get(request_id)
        if lock is not None:
            return lock
        with self._guard:
            lock = self._locks.get(request_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[request_id] = lock
                if len(self._locks) > _MAX_REQUEST_LOCKS:
                    self._evict_idle_locked(_MAX_REQUEST_LOCKS)
            return lock

    @asynccontextmanager
    async def acquire(self, request_id: str) -> AsyncIterator[None]:
        """Acquire the per-request lifecycle lock with refcount tracking.

        Canonical entry point for serialising
        ``scope``/``approve``/``reject`` transitions on a request id.
        Bumps an in-flight refcount before returning the Lock so a
        concurrent eviction sweep (triggered when the registry hits
        ``_MAX_REQUEST_LOCKS``) cannot drop the entry between this
        method receiving the Lock and the body's implicit
        ``await lock.acquire()``. Without that gate, the next caller
        for the same id would mint a fresh Lock and two callers would
        end up holding *different* Lock objects for the same request,
        breaking the per-id ordering invariant.

        Mirrors the pattern in
        :mod:`synthorg.api.rate_limits.in_memory` (``_lock_refs``).
        """
        lock = self._reserve(request_id)
        try:
            async with lock:
                yield
        finally:
            self._release_ref(request_id)

    def _reserve(self, request_id: str) -> asyncio.Lock:
        """Get-or-create the Lock and increment the in-flight refcount.

        Pairs with :meth:`_release_ref`. Both operations execute under
        ``self._guard`` so a concurrent eviction sweep observes the
        refcount bump and skips the entry.

        Returns:
            ``asyncio.Lock`` instance.
        """
        with self._guard:
            lock = self._locks.get(request_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[request_id] = lock
                if len(self._locks) > _MAX_REQUEST_LOCKS:
                    self._evict_idle_locked(_MAX_REQUEST_LOCKS)
            self._refs[request_id] = self._refs.get(request_id, 0) + 1
            return lock

    def _release_ref(self, request_id: str) -> None:
        """Drop one in-flight reference to the per-request Lock.

        The refs entry is removed (rather than left at 0) once the
        count drops to zero so a quiescent id contributes nothing to
        memory.
        """
        with self._guard:
            count = self._refs.get(request_id, 0) - 1
            if count <= 0:
                self._refs.pop(request_id, None)
            else:
                self._refs[request_id] = count

    def _evict_idle_locked(self, target_size: int) -> None:
        """Evict oldest idle entries down to ``target_size``.

        Caller must already hold ``self._guard``. Iterates the
        OrderedDict in insertion order; entries whose Lock is held OR
        whose in-flight refcount is non-zero are kept, so a long-running
        scope still in flight (or one whose caller has just received the
        Lock but not yet entered ``async with``) is never stranded on an
        evicted Lock object.
        """
        # Snapshot keys before mutating the OrderedDict during iteration.
        evicted = 0
        for request_id in list(self._locks.keys()):
            if len(self._locks) <= target_size:
                break
            lock = self._locks[request_id]
            if not lock.locked() and self._refs.get(request_id, 0) == 0:
                self._locks.pop(request_id, None)
                evicted += 1
        if evicted:
            # The cap was hit and idle entries were dropped; surface at
            # DEBUG so an operator can correlate a flood of unique,
            # never-advanced request ids against the bounded registry.
            logger.debug(
                REQUEST_LOCK_EVICTED_AT_CAP,
                evicted=evicted,
                cap=target_size,
            )

    def release_if_idle(self, request_id: str) -> None:
        """Drop the lock for ``request_id`` after a terminal transition.

        Called after the final ``save`` of a terminal state (approve,
        reject) so the registry does not accumulate one entry per
        lifetime request id. Only evicts when the lock is idle and
        no in-flight refcount remains -- a still-held or in-flight
        entry would strand a waiter who already holds a reference to
        the same :class:`asyncio.Lock` object. The caller must already
        have left the ``async with acquire`` block (or directly released
        the Lock returned by :meth:`get_or_create`) before invoking this
        helper, otherwise the ``locked()`` probe or refcount check
        reports the caller's own hold and the eviction is a no-op.
        """
        with self._guard:
            lock = self._locks.get(request_id)
            if lock is None:
                return
            if lock.locked() or self._refs.get(request_id, 0) > 0:
                # Caller violated the documented contract -- they're still
                # holding the lock when asking us to evict it. The no-op is
                # safe (the registry keeps the entry), but the violation is a
                # caller bug, so surface it at WARNING for operator visibility.
                logger.warning(
                    REQUEST_LOCK_RELEASE_SKIPPED_WHILE_HELD,
                    request_id=request_id,
                )
                return
            self._locks.pop(request_id, None)
