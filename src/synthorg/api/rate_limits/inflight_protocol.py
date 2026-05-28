"""Per-operation inflight-concurrency Protocol.

The ``InflightStore`` Protocol is the pluggable contract that the
per-operation concurrency middleware calls to acquire a permit.
Concrete implementations live in ``in_memory_inflight.py`` (default);
a Redis-backed adapter is reserved for cross-worker fairness.

The contract is deliberately minimal: one async context manager that
either yields a permit or raises on denial.  Adapters can be added
without touching middleware logic.
"""

from contextlib import AbstractAsyncContextManager
from typing import Protocol, runtime_checkable


@runtime_checkable
class InflightStore(Protocol):
    """Async per-key inflight-count limiter.

    Implementations must be safe to call concurrently from the same
    event loop.  A single logical bucket is identified by the ``key``
    argument; the middleware constructs keys of the form
    ``"{operation}:{user_or_ip}"`` so buckets never leak across
    operations or subjects.
    """

    def acquire(
        self,
        key: str,
        *,
        max_inflight: int,
    ) -> AbstractAsyncContextManager[None]:
        """Try to acquire a permit; returns an async context manager.

        On ``__aenter__``: if the current count for ``key`` is less
        than ``max_inflight``, increments the counter and returns.
        Otherwise raises
        :class:`synthorg.core.domain_errors.ConcurrencyLimitExceededError`
        **before** yielding control, so the request never executes the
        handler.

        On ``__aexit__``: always decrements the counter so a permit
        leaked by an exception does not wedge the bucket.  Releasing
        below zero is a logical error and is clamped at zero with a
        warning -- refusing the decrement would leak the bucket in
        the opposite direction.

        Args:
            key: Bucket identifier.  Stable per (operation, subject).
            max_inflight: Maximum concurrent requests allowed.  Must
                be positive.

        Returns:
            An async context manager.  Entering it either succeeds
            (permit held) or raises
            :class:`ConcurrencyLimitExceededError`.
        """
        ...

    async def close(self) -> None:
        """Release any background resources (connections, timers)."""
        ...


__all__ = ["InflightStore"]
