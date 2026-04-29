"""Reranker cache with TTL and LRU eviction.

Thread-safe in-memory cache for query-specific re-ranking results.

The cache stores only the ordered sequence of candidate IDs produced
by the LLM (not the full ``RetrievalCandidate`` objects).  Callers
reapply the cached ordering to the current candidate set, ensuring
fresh candidate state (content, scores) is always used on cache
hits -- stale tuples never leak back.
"""

import asyncio

from synthorg.core.clock import Clock, SystemClock
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_RERANK_CACHE_HIT,
    MEMORY_RERANK_CACHE_MISS,
)
from synthorg.observability.metrics_hub import record_cache_operation

_CACHE_NAME = "reranker"

logger = get_logger(__name__)

_DEFAULT_TTL_SECONDS = 3600
_DEFAULT_MAX_SIZE = 1000


class RerankerCache:
    """LRU cache with TTL for re-ranked retrieval results.

    Stores ``(id_ordering, timestamp, last_access)`` triples keyed by
    a hash of the query text and candidate entry IDs.  Only the ordered
    sequence of entry IDs is retained; callers reapply this ordering
    to fresh candidate objects on cache hits to prevent stale object
    leaks.  Expired entries are evicted on access; when ``max_size``
    is exceeded, the least recently accessed entry is evicted.

    Thread-safe via ``asyncio.Lock``.

    Args:
        ttl_seconds: Time-to-live in seconds for cached results.
        max_size: Maximum number of cached entries.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        max_size: int = _DEFAULT_MAX_SIZE,
        clock: Clock | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            msg = "ttl_seconds must be positive"
            raise ValueError(msg)
        if max_size <= 0:
            msg = "max_size must be positive"
            raise ValueError(msg)
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._clock: Clock = clock or SystemClock()
        self._store: dict[
            str,
            tuple[tuple[str, ...], float, float],
        ] = {}
        self._lock = asyncio.Lock()

    async def get(
        self,
        key: str,
    ) -> tuple[str, ...] | None:
        """Retrieve cached ID ordering, or ``None`` on miss/expiry.

        Args:
            key: Cache key (hash of query + candidate IDs).

        Returns:
            Cached ordering of candidate IDs or ``None``.
        """
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                logger.debug(
                    MEMORY_RERANK_CACHE_MISS,
                    key=key[:16],
                )
                record_cache_operation(cache_name=_CACHE_NAME, outcome="miss")
                return None
            id_order, created_at, _ = entry
            if self._clock.monotonic() - created_at > self._ttl:
                del self._store[key]
                logger.debug(
                    MEMORY_RERANK_CACHE_MISS,
                    key=key[:16],
                    reason="expired",
                )
                record_cache_operation(cache_name=_CACHE_NAME, outcome="miss")
                return None
            # Update last access time
            self._store[key] = (id_order, created_at, self._clock.monotonic())
            logger.debug(
                MEMORY_RERANK_CACHE_HIT,
                key=key[:16],
            )
            record_cache_operation(cache_name=_CACHE_NAME, outcome="hit")
            return id_order

    async def put(
        self,
        key: str,
        id_order: tuple[str, ...],
    ) -> None:
        """Store reranked ID ordering in the cache.

        Purges expired entries first, then evicts the least recently
        accessed entry when ``max_size`` is still exceeded.  Purging
        before LRU prevents a stale (but not-yet-GCed) entry from
        displacing a live ranking.

        Args:
            key: Cache key.
            id_order: Ordered sequence of candidate entry IDs.
        """
        async with self._lock:
            now = self._clock.monotonic()
            # Purge expired entries first so they don't protect
            # themselves from eviction by occupying slots.
            expired_keys = [
                k
                for k, (_, created_at, _) in self._store.items()
                if now - created_at > self._ttl
            ]
            for k in expired_keys:
                del self._store[k]
            self._store[key] = (id_order, now, now)
            if len(self._store) > self._max_size:
                self._evict_lru()

    async def invalidate(self, key: str) -> None:
        """Remove a specific key from the cache.

        Args:
            key: Cache key to invalidate.
        """
        async with self._lock:
            self._store.pop(key, None)

    async def clear(self) -> None:
        """Remove all entries from the cache."""
        async with self._lock:
            self._store.clear()

    @property
    def size(self) -> int:
        """Current number of cached entries."""
        return len(self._store)

    def _evict_lru(self) -> None:
        """Evict the least recently accessed entry."""
        if not self._store:
            return
        oldest_key = min(
            self._store,
            key=lambda k: self._store[k][2],  # last_access timestamp
        )
        del self._store[oldest_key]
        record_cache_operation(cache_name=_CACHE_NAME, outcome="evict")
