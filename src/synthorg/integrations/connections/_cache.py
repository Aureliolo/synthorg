"""Cache + per-name lock behaviour for :class:`ConnectionCatalog`.

The in-memory connection snapshot, its double-checked-locking reload,
and the per-connection mutation locks live in this mixin so the main
catalog module stays focused on CRUD + credential resolution. The mixin
owns ``_cache`` / ``_cache_lock`` / ``_cache_valid`` / ``_name_locks`` /
``_name_locks_lock`` (all initialised by the host ``__init__``) and reads
``_repo``; the ``TYPE_CHECKING`` block declares that surface so ``mypy``
type-checks the mixin in isolation.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.integrations.connections.models import Connection
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import paginate

if TYPE_CHECKING:
    from synthorg.persistence.connection_protocol import ConnectionRepository


class ConnectionCacheMixin:
    """Cache + lock methods mixed into :class:`ConnectionCatalog`."""

    if TYPE_CHECKING:
        _repo: ConnectionRepository
        _cache: dict[str, Connection]
        _cache_lock: asyncio.Lock
        _cache_valid: bool
        _name_locks: dict[str, asyncio.Lock]
        _name_locks_lock: asyncio.Lock

    async def rebind_repository(self, repository: ConnectionRepository) -> None:
        """Swap the underlying repository and invalidate the cache.

        Used by the API lifecycle hook to graduate the catalog from the
        startup-window ``InMemoryConnectionRepository`` stub (installed
        before ``persistence.connect()`` succeeds) to the real
        backend-bound repository once persistence is live.

        Args:
            repository: The newly-available persistence-backed
                ``ConnectionRepository`` to take over from this point on.
        """
        # Hold ``_cache_lock`` for the swap so a concurrent
        # ``_ensure_cache`` cannot observe a half-swapped state where
        # ``self._repo`` is already the new backend but ``self._cache``
        # still carries entries seeded from the in-memory stub.
        async with self._cache_lock:
            self._repo = repository
            self._cache = {}
            self._cache_valid = False

    async def _ensure_cache(self) -> None:
        """Populate the cache from persistence if invalid."""
        if self._cache_valid:
            return
        async with self._cache_lock:
            # Re-check under lock (double-checked locking)
            if not self._cache_valid:
                # The catalog needs every persisted connection to
                # satisfy synchronous ``by_name`` lookups, so page
                # through the full set: a single capped read would
                # silently drop connections past the backend page cap.
                collected: list[Connection] = []
                async for page in paginate(
                    lambda limit, offset: self._repo.list_items(
                        limit=limit, offset=offset
                    ),
                    page_size=DEFAULT_PAGE_SIZE,
                ):
                    collected.extend(page)
                self._cache = {c.name: c for c in collected}
                self._cache_valid = True

    async def _invalidate_cache(self) -> None:
        """Mark the cached connection snapshot stale (forces a reload).

        Acquires ``_cache_lock`` so the invalidation is serialised
        against ``_ensure_cache``: without it, a mutation that
        invalidates while a concurrent ``_ensure_cache`` is mid-reload
        (holding the lock, awaiting the repository read) would be
        clobbered by that reload's trailing ``_cache_valid = True``,
        leaving the stale snapshot marked fresh.
        """
        async with self._cache_lock:
            self._cache_valid = False

    def get_cached(self, name: str) -> Connection | None:
        """Return the cached connection for ``name`` without populating.

        Synchronous peek into the in-memory cache; returns ``None`` when
        the cache has not been primed yet or the name is unknown. Use
        when callers prefer a best-effort read over forcing a
        repository fetch (e.g. boot-time rate-limit coordinators).

        Args:
            name: Connection name to peek.

        Returns:
            The cached ``Connection``, or ``None`` when the cache is
            unprimed or the name is unknown.
        """
        if not self._cache_valid:
            return None
        return self._cache.get(name)

    async def _lock_for(self, name: str) -> asyncio.Lock:
        """Return (or create) the mutation lock for a connection name.

        Args:
            name: Connection name to lock.

        Returns:
            The per-name ``asyncio.Lock`` (created on first use).
        """
        async with self._name_locks_lock:
            lock = self._name_locks.get(name)
            if lock is None:
                lock = asyncio.Lock()
                self._name_locks[name] = lock
            return lock
