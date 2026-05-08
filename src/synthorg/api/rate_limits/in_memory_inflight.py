"""In-memory per-operation inflight limiter.

Each bucket holds an integer counter and a per-key lock so the check-
and-increment is atomic.  On ``acquire``, if the counter is below
``max_inflight`` it is incremented and a context manager is returned;
on context exit the counter is decremented.  If the counter is at
``max_inflight``, the acquire raises
:class:`ConcurrencyLimitExceededError` before yielding control, so
the handler never runs.

The store is async-safe via per-key ``asyncio.Lock`` plus a meta-lock
that guards lazy lock creation.  Buckets with a zero counter and no
pending waiters are evicted by a lightweight sweep on every N-th
acquire to bound memory growth.
"""

import asyncio
from collections.abc import AsyncIterator  # noqa: TC003
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Final

from synthorg.api.rate_limits.inflight_protocol import InflightStore
from synthorg.core.domain_errors import ConcurrencyLimitExceededError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_GUARD_DENIED,
    API_REQUEST_ERROR,
)

logger = get_logger(__name__)

_GC_EVERY_N_ACQUIRES: Final[int] = 1024  # lint-allow: magic-numbers -- bootstrap
_MIN_RETRY_AFTER_SECONDS: Final[int] = 1  # lint-allow: magic-numbers -- bootstrap


class InMemoryInflightStore(InflightStore):
    """Process-local per-operation inflight limiter.

    Not shared across processes -- with multiple Litestar workers, each
    worker maintains an independent counter.  That is acceptable for
    per-operation concurrency caps where the goal is to bound per-user
    pressure on expensive local resources (file I/O during fine-tune,
    remote-API fanout during model pull); the global two-tier limiter
    in ``api/app.py`` handles cross-worker coordination separately.

    Wire up a Redis-backed adapter via :func:`build_inflight_store`
    when cross-worker fairness becomes a requirement.
    """

    def __init__(self) -> None:
        """Initialise an empty counter store."""
        self._counters: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._meta_lock: asyncio.Lock = asyncio.Lock()
        self._acquires_since_gc: int = 0
        # Reference count of in-flight ``_acquire_or_raise`` /
        # ``_release`` calls per key, used to gate GC so a coroutine
        # that has just received a lock from ``_get_lock`` (but not
        # yet awaited ``async with lock``) can never have it evicted
        # from underneath itself. Without this, ``_gc_cold_buckets``
        # could drop the unlocked lock and the next
        # ``_get_lock(same_key)`` would mint a fresh lock, leaving two
        # concurrent callers holding *different* lock objects for the
        # same key. Mirrors the pattern in
        # :class:`InMemorySlidingWindowStore`.
        self._lock_refs: dict[str, int] = {}

    def acquire(
        self,
        key: str,
        *,
        max_inflight: int,
    ) -> AbstractAsyncContextManager[None]:
        """Return an async context manager that holds one permit."""
        if max_inflight <= 0:
            msg = "max_inflight must be positive"
            # Validation error at decorator/config time, not runtime -- use
            # the startup event constant so these surface with other boot
            # misconfigurations rather than request-path errors.
            logger.warning(
                API_APP_STARTUP,
                error_type="inflight_invalid_config",
                limiter="InMemoryInflightStore",
                key=key,
                max_inflight=max_inflight,
                error=msg,
            )
            raise ValueError(msg)
        return self._permit(key, max_inflight=max_inflight)

    @asynccontextmanager
    async def _permit(
        self,
        key: str,
        *,
        max_inflight: int,
    ) -> AsyncIterator[None]:
        """Context manager: acquire on enter, release on exit."""
        await self._acquire_or_raise(key, max_inflight=max_inflight)
        try:
            yield
        finally:
            await self._release(key)

    async def _acquire_or_raise(
        self,
        key: str,
        *,
        max_inflight: int,
    ) -> None:
        """Atomically check-and-increment or raise.

        Raises:
            ConcurrencyLimitExceededError: When ``_counters[key]`` is
                already at ``max_inflight``.  Carries ``retry_after=1``
                because inflight holds release on handler completion,
                which is unpredictable -- 1 second is a cheap
                re-poll interval that never hot-loops and usually
                matches the duration of the ops this guards.
        """
        lock = await self._get_lock(key)
        try:
            async with lock:
                current = self._counters.get(key, 0)
                if current >= max_inflight:
                    logger.warning(
                        API_GUARD_DENIED,
                        guard="per_op_concurrency",
                        key=key,
                        max_inflight=max_inflight,
                        current=current,
                        retry_after=_MIN_RETRY_AFTER_SECONDS,
                    )
                    # Public message intentionally opaque: the exact
                    # ``current`` / ``max_inflight`` counts go to the
                    # audit log above so operators can diagnose
                    # saturation, but surfacing them in the response
                    # body would let a caller coordinate requests to
                    # exhaust the limit precisely or infer how many
                    # concurrent requests other subjects currently
                    # hold for the same operation.
                    raise ConcurrencyLimitExceededError(
                        retry_after=_MIN_RETRY_AFTER_SECONDS,
                    )
                self._counters[key] = current + 1
            # GC counter bump happens only on successful acquire;
            # denied requests don't allocate new counters and so
            # don't contribute to bucket growth. Counter + threshold
            # check run under the meta-lock to avoid redundant
            # concurrent sweeps.
            should_gc = False
            async with self._meta_lock:
                self._acquires_since_gc += 1
                if self._acquires_since_gc >= _GC_EVERY_N_ACQUIRES:
                    self._acquires_since_gc = 0
                    should_gc = True
            if should_gc:
                await self._gc_cold_buckets()
        finally:
            # Drop the ref AFTER we are done touching the lock and
            # the GC bump; only then is it safe for the GC to evict
            # the bucket. Pairs with the increment inside ``_get_lock``.
            await self._release_lock_ref(key)

    async def _release(self, key: str) -> None:
        """Decrement the counter; clamp at zero on underflow."""
        lock = await self._get_lock(key)
        try:
            async with lock:
                current = self._counters.get(key, 0)
                if current <= 0:
                    # Releasing below zero is a logical error -- it
                    # means we held a permit we never acquired. Clamp
                    # to zero (the best recovery: the bucket returns
                    # to "empty") and log loudly so the bug surfaces.
                    # Refusing the decrement would leak the bucket
                    # in the opposite direction next acquire.
                    logger.warning(
                        API_REQUEST_ERROR,
                        error_type="inflight_negative_release",
                        limiter="InMemoryInflightStore",
                        key=key,
                        error="release called with counter already at 0",
                    )
                    self._counters[key] = 0
                    return
                self._counters[key] = current - 1
        finally:
            await self._release_lock_ref(key)

    async def close(self) -> None:
        """Clear all counters and locks."""
        async with self._meta_lock:
            self._counters.clear()
            self._locks.clear()
            self._lock_refs.clear()
            self._acquires_since_gc = 0

    async def _get_lock(self, key: str) -> asyncio.Lock:
        """Return the per-key lock, creating it under the meta-lock.

        Always acquires ``self._meta_lock``: the previous fast-path
        (``self._locks.get`` outside the lock) opened a TOCTOU window
        between the lock retrieval and the caller's ``async with
        lock`` -- ``_gc_cold_buckets`` could observe an unlocked,
        zero-counter bucket and evict it, the next ``_get_lock`` for
        the same key would mint a fresh lock, and two concurrent
        callers would hold different locks for the same key.

        Increments ``_lock_refs[key]`` so a concurrent
        :meth:`_gc_cold_buckets` cannot evict the lock between this
        return and the caller's ``async with``. The caller MUST pair
        every successful ``_get_lock`` with a
        :meth:`_release_lock_ref`.
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
        in-flight ``_acquire_or_raise`` / ``_release``.
        """
        async with self._meta_lock:
            count = self._lock_refs.get(key, 0) - 1
            if count <= 0:
                self._lock_refs.pop(key, None)
            else:
                self._lock_refs[key] = count

    async def _gc_cold_buckets(self) -> None:
        """Drop buckets with counter == 0 and no locked / referenced lock.

        Also reclaims orphan locks -- entries in ``self._locks`` that
        have no matching counter -- so they do not leak memory.

        ``self._lock_refs`` gates eviction in addition to
        ``lock.locked()``: a coroutine that just received the lock
        from :meth:`_get_lock` but has not yet entered ``async with
        lock`` would otherwise be deprived of its lock between those
        two points (the GC sees ``locked()=False`` because the
        ``async with`` has not started, but a ``ref > 0`` indicates
        the bucket is in use).
        """
        async with self._meta_lock:
            try:
                dead: list[str] = []
                for key, count in self._counters.items():
                    if count <= 0:
                        lock = self._locks.get(key)
                        if (lock is None or not lock.locked()) and self._lock_refs.get(
                            key, 0
                        ) == 0:
                            dead.append(key)
                for key in dead:
                    self._counters.pop(key, None)
                    lock = self._locks.get(key)
                    if (
                        lock is not None
                        and not lock.locked()
                        and self._lock_refs.get(key, 0) == 0
                    ):
                        self._locks.pop(key, None)
                orphan_lock_keys = [
                    key for key in list(self._locks.keys()) if key not in self._counters
                ]
                for key in orphan_lock_keys:
                    lock = self._locks.get(key)
                    if (
                        lock is not None
                        and not lock.locked()
                        and self._lock_refs.get(key, 0) == 0
                    ):
                        self._locks.pop(key, None)
            except asyncio.CancelledError, MemoryError, RecursionError:
                # Non-recoverable: propagate so shutdown / OOM is not hidden.
                raise
            except Exception as exc:
                # ``safe_error_description`` strips attacker-controllable
                # bytes from the serialised error so a misbehaving
                # bucket-key or exception subclass cannot inject text
                # that breaks the structured log stream.
                logger.warning(
                    API_REQUEST_ERROR,
                    error_type="inflight_gc_failed",
                    error=safe_error_description(exc),
                )
