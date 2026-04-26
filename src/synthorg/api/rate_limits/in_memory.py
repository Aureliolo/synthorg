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
        if max_requests <= 0:
            msg = "max_requests must be positive"
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
        if window_seconds <= 0:
            msg = "window_seconds must be positive"
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

        outcome: RateLimitOutcome
        lock = await self._get_lock(key)
        async with lock:
            now = time.monotonic()
            bucket = self._buckets.setdefault(key, _Bucket())
            # Remember the largest observed window per key; GC uses this
            # to avoid prematurely evicting a long-window bucket when a
            # short-window acquire triggers a sweep.
            bucket.window_seconds = max(bucket.window_seconds, window_seconds)
            cutoff = now - float(window_seconds)
            while bucket.timestamps and bucket.timestamps[0] <= cutoff:
                bucket.timestamps.popleft()
            if len(bucket.timestamps) >= max_requests:
                oldest = bucket.timestamps[0]
                # Minimum 0.001s so a client seeing retry_after=0 never hot-loops
                # on sub-millisecond clock jitter while a window is still active.
                retry_after = max(oldest + float(window_seconds) - now, 0.001)
                outcome = RateLimitOutcome(
                    allowed=False,
                    retry_after_seconds=retry_after,
                    remaining=0,
                )
            else:
                bucket.timestamps.append(now)
                remaining = max(max_requests - len(bucket.timestamps), 0)
                outcome = RateLimitOutcome(
                    allowed=True,
                    retry_after_seconds=None,
                    remaining=remaining,
                )

        # GC counter increments on every acquire -- allowed AND denied --
        # so a key under sustained deny pressure still triggers periodic
        # cold-bucket sweeps.  Counter + threshold check run under the
        # meta-lock to avoid redundant concurrent sweeps.
        should_gc = False
        async with self._meta_lock:
            self._acquires_since_gc += 1
            if self._acquires_since_gc >= _GC_EVERY_N_ACQUIRES:
                self._acquires_since_gc = 0
                should_gc = True
        if should_gc:
            await self._gc_cold_buckets()

        return outcome

    async def close(self) -> None:
        """Clear all buckets and locks."""
        async with self._meta_lock:
            self._buckets.clear()
            self._locks.clear()
            self._acquires_since_gc = 0

    async def _get_lock(self, key: str) -> asyncio.Lock:
        """Return the per-key lock, creating it under the meta-lock.

        Always acquires ``self._meta_lock`` rather than peeking at
        ``self._locks`` first. The unlocked fast-path was correct under
        CPython's GIL but fragile on alternative interpreters where
        ``dict.get`` is not guaranteed atomic, and the cost of one
        async-lock acquire per ``acquire()`` is negligible against the
        bucket-mutation work that follows (#1599).
        """
        async with self._meta_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

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
                dead: list[str] = []
                for key, bucket in self._buckets.items():
                    horizon = max(
                        bucket.window_seconds * 2,
                        _MIN_GC_HORIZON_SECONDS,
                    )
                    cutoff = now - float(horizon)
                    if not bucket.timestamps or bucket.timestamps[-1] <= cutoff:
                        dead.append(key)
                for key in dead:
                    self._buckets.pop(key, None)
                    # Only drop the lock if no task is holding it -- a
                    # locked entry means an in-flight acquire that must
                    # observe the same lock object.
                    lock = self._locks.get(key)
                    if lock is not None and not lock.locked():
                        self._locks.pop(key, None)
                # Sweep orphan locks (keys in _locks but not in _buckets).
                orphan_lock_keys = [
                    key for key in list(self._locks.keys()) if key not in self._buckets
                ]
                for key in orphan_lock_keys:
                    lock = self._locks.get(key)
                    if lock is not None and not lock.locked():
                        self._locks.pop(key, None)
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
