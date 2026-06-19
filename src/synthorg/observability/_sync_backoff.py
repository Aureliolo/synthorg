"""Bounded exponential backoff for the sync logging-handler threads.

Both ``HttpBatchHandler`` and ``OtlpHandler`` run inside a stdlib
``logging.Handler`` worker thread (Pattern C/Sync in
``docs/reference/retry-patterns.md``), where the async
``GeneralRetryHandler`` is unreachable. They shared an identical
``delay(attempt) = min(base * factor**attempt, cap)`` formula; it lives
here once so the two sinks stay in lock-step.
"""

from typing import Final

_RETRY_BACKOFF_BASE_SECONDS: Final[float] = 0.5
_RETRY_BACKOFF_FACTOR: Final[int] = 2
_RETRY_BACKOFF_CAP_SECONDS: Final[float] = 8.0
_RETRY_BACKOFF_CAP_ATTEMPT: Final[int] = 4
"""First attempt at which the exponential already meets the cap
(``0.5 * 2**4 == 8.0``). At or beyond it the delay is always the cap, so
short-circuit before ``float(2**attempt)`` to avoid an ``OverflowError``
should a caller ever pass an unbounded attempt count."""


def backoff_delay(attempt: int) -> float:
    """Bounded exponential backoff for retry *attempt* (0-indexed).

    Args:
        attempt: The zero-indexed retry attempt number.

    Returns:
        Seconds to wait before the next attempt, capped at
        ``_RETRY_BACKOFF_CAP_SECONDS``.
    """
    if attempt >= _RETRY_BACKOFF_CAP_ATTEMPT:
        return _RETRY_BACKOFF_CAP_SECONDS
    delay = _RETRY_BACKOFF_BASE_SECONDS * float(_RETRY_BACKOFF_FACTOR**attempt)
    return min(delay, _RETRY_BACKOFF_CAP_SECONDS)
