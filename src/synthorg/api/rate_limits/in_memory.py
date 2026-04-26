"""In-memory sliding-window rate limiter.

Each bucket holds a deque of monotonic timestamps.  On each ``acquire``
call, timestamps older than ``window_seconds`` are evicted; if the
remaining count is below ``max_requests``, the new timestamp is appended
and the request is allowed.  Otherwise the oldest remaining timestamp
gives the exact number of seconds the caller must wait.

The store is async-safe via a per-key ``asyncio.Lock``.  Buckets with
no activity for ``max(bucket_window * 2, 60)`` seconds are evicted by
a lightweight sweep on every N-th acquire to bound memory growth.  The
eviction horizon is computed per bucket from the last observed
``window_seconds`` so short-window operations cannot evict long-window
buckets prematurely.
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Final

from synthorg.api.rate_limits.protocol import RateLimitOutcome, SlidingWindowStore
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_REQUEST_ERROR

logger = get_logger(__name__)

_GC_EVERY_N_ACQUIRES: Final[int] = 1024
_MIN_GC_HORIZON_SECONDS: Final[int] = 60


@dataclass
class _Bucket:
    """Per-key bucket state.

    Tracks the timestamps and the last observed ``window_seconds`` so
    the GC can compute a bucket-local eviction horizon instead of
    trusting the current acquire's window.
    """

    timestamps: deque[float] = field(default_factory=deque)
    window_seconds: int = _MIN_GC_HORIZON_SECONDS


class InMemorySlidingWindowStore(SlidingWindowStore):
    """Process-local sliding-window limiter.

    Not shared across processes -- with multiple Litestar workers, each
    worker maintains an independent bucket.  That is acceptable for
    per-operation throttling where global coordination is not required;
    the global two-tier limiter in ``api/app.py`` handles cross-worker
    coordination separately.
    """

    def __init__(self) -> None:
        """Initialise an empty bucket store."""
        self._buckets: dict[str, _Bucket] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # Reference count of in-flight ``acquire`` calls per key, used
        # to gate GC so a coroutine that has just received a lock from
        # ``_get_lock`` (but not yet awaited ``acquire``) can never
        # have it evicted from underneath itself. ``lock.locked()``
        # alone is insufficient: between ``_get_lock`` returning and
        # ``async with lock`` actually blocking, a GC sweep could drop
        # the unlocked lock and the next ``_get_lock(same_key)`` would
        # mint a fresh lock, leaving two concurrent ``acquire`` calls
        # holding *different* lock objects for the same key.
        self._lock_refs: dict[str, int] = {}
        self._meta_lock: asyncio.Lock = asyncio.Lock()
        self._acquires_since_gc: int = 0

    async def acquire(
        self,
        key: str,
        *,
        max_requests: int,
        window_seconds: int,
    ) -> RateLimitOutcome:
        """Record one hit on ``key`` against the ``max_requests`` budget."""
        self._validate_window_config(
            key=key,
            max_requests=max_requests,
            window_seconds=window_seconds,
        )
        lock = await self._get_lock(key)
        try:
            async with lock:
                now = time.monotonic()
                bucket = self._buckets.setdefault(key, _Bucket())
                outcome = self._apply_sliding_window(
                    bucket=bucket,
                    now=now,
                    max_requests=max_requests,
                    window_seconds=window_seconds,
                )
        finally:
            await self._release_lock_and_cleanup(key)
        if await self._maybe_schedule_gc():
            await self._gc_cold_buckets()
        return outcome

    def _validate_window_config(
        self,
        *,
        key: str,
        max_requests: int,
        window_seconds: int,
    ) -> None:
        """Reject non-positive limits with a structured warning."""
        if max_requests > 0 and window_seconds > 0:
            return
        msg = (
            "max_requests must be positive"
            if max_requests <= 0
            else "window_seconds must be positive"
        )
        logger.warning(
            API_REQUEST_ERROR,
            error_type="rate_limit_invalid_config",
            limiter="InMemorySlidingWindowStore",
            key=key,
            max_requests=max_requests,
            window_seconds=window_seconds,
            error=msg,
        )
        raise ValueError(msg)

    def _apply_sliding_window(
        self,
        *,
        bucket: _Bucket,
        now: float,
        max_requests: int,
        window_seconds: int,
    ) -> RateLimitOutcome:
        """Advance the bucket's deque and decide allow / deny.

        Caller MUST hold the per-key lock so the deque mutations and
        the decision are atomic against concurrent ``acquire`` calls
        on the same key.

        Pruning uses the bucket's *largest observed* window so a
        short-window acquire on a shared key cannot evict events that
        a longer-window concurrent acquire still needs. The
        allow/deny decision counts only events inside the *current*
        call's window, leaving older-but-still-relevant events in the
        deque for any larger-window acquire on the same key.
        """
        # Track the largest observed window per key (also used by GC).
        bucket.window_seconds = max(bucket.window_seconds, window_seconds)
        # Prune by the largest window so events still relevant to a
        # longer-window acquire are not lost.
        prune_cutoff = now - float(bucket.window_seconds)
        while bucket.timestamps and bucket.timestamps[0] <= prune_cutoff:
            bucket.timestamps.popleft()
        # Decide allow/deny against the CURRENT call's window. Iterate
        # from the right (newest) and stop at the first event outside
        # the window; events earlier than that count toward longer
        # windows but not this one.
        decision_cutoff = now - float(window_seconds)
        in_window = 0
        oldest_in_window: float | None = None
        for ts in reversed(bucket.timestamps):
            if ts <= decision_cutoff:
                break
            in_window += 1
            oldest_in_window = ts
        if in_window >= max_requests and oldest_in_window is not None:
            # Minimum 0.001s so a client seeing retry_after=0 never
            # hot-loops on sub-millisecond clock jitter while a window
            # is still active.
            retry_after = max(
                oldest_in_window + float(window_seconds) - now,
                0.001,
            )
            return RateLimitOutcome(
                allowed=False,
                retry_after_seconds=retry_after,
                remaining=0,
            )
        bucket.timestamps.append(now)
        remaining = max(max_requests - in_window - 1, 0)
        return RateLimitOutcome(
            allowed=True,
            retry_after_seconds=None,
            remaining=remaining,
        )

    async def _release_lock_and_cleanup(self, key: str) -> None:
        """Drop the per-acquire ``_lock_refs`` reference cancel-safely.

        :meth:`_release_lock_ref` itself awaits ``_meta_lock``, so a
        cancellation arriving in ``acquire``'s ``finally`` could exit
        before the decrement landed and leak the entry, leaving the
        lock for *key* permanently un-evictable by GC. ``asyncio.shield``
        guarantees the cleanup completes before the cancel is re-raised.
        """
        await asyncio.shield(self._release_lock_ref(key))

    async def _maybe_schedule_gc(self) -> bool:
        """Increment the acquire counter; report whether GC should run.

        Counter + threshold check run under the meta-lock to avoid
        redundant concurrent sweeps. Increments on every acquire --
        allowed AND denied -- so a key under sustained deny pressure
        still triggers periodic cold-bucket sweeps.
        """
        async with self._meta_lock:
            self._acquires_since_gc += 1
            if self._acquires_since_gc >= _GC_EVERY_N_ACQUIRES:
                self._acquires_since_gc = 0
                return True
        return False

    async def close(self) -> None:
        """Clear all buckets and locks."""
        async with self._meta_lock:
            self._buckets.clear()
            self._locks.clear()
            self._lock_refs.clear()
            self._acquires_since_gc = 0

    async def _get_lock(self, key: str) -> asyncio.Lock:
        """Return the per-key lock, creating it under the meta-lock.

        Always acquires ``self._meta_lock`` rather than peeking at
        ``self._locks`` first. The unlocked fast-path was correct under
        CPython's GIL but fragile on alternative interpreters where
        ``dict.get`` is not guaranteed atomic, and the cost of one
        async-lock acquire per ``acquire()`` is negligible against the
        bucket-mutation work that follows (#1599).

        Increments ``_lock_refs[key]`` so a concurrent
        :meth:`_gc_cold_buckets` cannot evict the lock between this
        return and the caller's ``async with``. The caller MUST pair
        every successful ``_get_lock`` with a
        :meth:`_release_lock_ref` (see ``finally`` block in
        :meth:`acquire`).
        """
        async with self._meta_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            self._lock_refs[key] = self._lock_refs.get(key, 0) + 1
            return lock

    async def _release_lock_ref(self, key: str) -> None:
        """Drop one reference; remove the entry when it hits zero.

        Pairs with :meth:`_get_lock`. The ``_lock_refs`` entry is
        deleted (rather than left at 0) so a quiescent key contributes
        nothing to memory; the dict only holds keys with at least one
        in-flight ``acquire``.
        """
        async with self._meta_lock:
            count = self._lock_refs.get(key, 0) - 1
            if count <= 0:
                self._lock_refs.pop(key, None)
            else:
                self._lock_refs[key] = count

    async def _gc_cold_buckets(self) -> None:
        """Drop buckets (and locks) that have been empty for twice the window.

        The horizon is computed per bucket from its own last-observed
        ``window_seconds`` so a short-window sweep cannot evict
        long-window buckets prematurely.  Also reclaims orphan locks --
        entries in ``self._locks`` that have no matching bucket (e.g.
        a cancelled ``acquire`` that created the lock before the bucket
        was materialised) -- so they do not leak memory across the
        process lifetime.
        """
        async with self._meta_lock:
            try:
                now = time.monotonic()
                dead = [
                    key
                    for key, bucket in self._buckets.items()
                    if self._is_bucket_cold(bucket, now)
                ]
                for key in dead:
                    self._buckets.pop(key, None)
                    self._maybe_drop_idle_lock(key)
                # Sweep orphan locks (keys in _locks but not in _buckets).
                orphan_lock_keys = [
                    key for key in list(self._locks.keys()) if key not in self._buckets
                ]
                for key in orphan_lock_keys:
                    self._maybe_drop_idle_lock(key)
            except asyncio.CancelledError, MemoryError, RecursionError:
                # Non-recoverable: propagate so shutdown / OOM is not hidden.
                raise
            except Exception as exc:
                # GC is best-effort -- never block acquire progress.
                # ``safe_error_description`` scrubs attacker-controllable
                # bytes from the serialised error so a misbehaving
                # bucket-key or exception subclass cannot inject text
                # that breaks the structured log stream (SEC-1).
                logger.warning(
                    API_REQUEST_ERROR,
                    error_type="rate_limit_gc_failed",
                    error=safe_error_description(exc),
                )

    def _is_bucket_cold(self, bucket: _Bucket, now: float) -> bool:
        """Return True when the bucket is past its per-key GC horizon.

        ``window_seconds`` is the largest observed window for the
        bucket; doubling it gives the cooldown horizon. Empty buckets
        are always considered cold.
        """
        horizon = max(bucket.window_seconds * 2, _MIN_GC_HORIZON_SECONDS)
        cutoff = now - float(horizon)
        return not bucket.timestamps or bucket.timestamps[-1] <= cutoff

    def _maybe_drop_idle_lock(self, key: str) -> None:
        """Remove the lock for *key* iff no task holds or references it.

        ``lock.locked()`` alone is too weak: a coroutine that just got
        the lock from :meth:`_get_lock` but has not yet awaited
        ``async with`` would be deprived of its lock, and the next
        ``_get_lock(same_key)`` would mint a fresh one -- two
        ``acquire`` calls holding different locks for the same key.
        Caller MUST hold ``self._meta_lock``.
        """
        lock = self._locks.get(key)
        if lock is not None and not lock.locked() and self._lock_refs.get(key, 0) == 0:
            self._locks.pop(key, None)
