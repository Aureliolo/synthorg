"""General-purpose async retry handler for non-provider resilience.

The :class:`synthorg.providers.resilience.RetryHandler` lives at the
provider boundary and is coupled to ``ProviderError.is_retryable``
semantics.  This module provides the equivalent primitive for code
paths that cannot depend on provider error types but DO need bounded
retry with exponential backoff for **transient I/O failures**:
``workers.dispatcher`` (NATS publish), ``meta.telemetry.emitter``
(collector POST).

Callers supply a ``retryable`` predicate that classifies any
exception, plus the standard backoff parameters (``base``, ``cap``,
``jitter``).  The handler runs the retry loop, sleeps between
attempts using exponential backoff with optional jitter, and emits a
single structured log event on each retry attempt carrying the
caller-provided ``log_ctx``.

Sites that intentionally do NOT use this helper
================================================

These retry loops live elsewhere in the codebase but solve a
different problem.  Reach for this helper only when you have a true
transient-failure retry with temporal backoff.

- ``engine.decomposition.llm`` and ``engine.workspace.semantic_llm``
  run an **LLM self-correction loop**: each retry sends a richer
  message that includes the previous failed response.  The retry
  is semantic, not temporal -- there is no sleep between attempts
  and no exponential backoff makes sense.  If you find yourself
  building another self-correction loop, extract a dedicated helper
  rather than wedging it through ``base=0`` here.

- ``persistence.postgres.decision_repo._append_with_retry`` retries
  on ``UniqueViolation`` only when the constraint name indicates a
  ``(task_id, version)`` race; other unique-constraint failures map
  to ``DuplicateRecordError`` immediately.  The retry is
  contention-driven, not transient-failure-driven, and the error
  classification is intricate enough that an inline loop is clearer.

- ``observability.http_handler.HttpBatchHandler`` runs inside a
  stdlib logging-handler thread using synchronous
  ``urllib.request``.  This async helper cannot be awaited from
  there.  Bootstrap-tier code keeps its own retry loop.
"""

import asyncio
import random
from typing import TYPE_CHECKING, TypeVar

from synthorg.observability import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger(__name__)

T = TypeVar("T")


class GeneralRetryHandler:
    """Wrap an async callable with bounded retry + exponential backoff.

    Args:
        retryable: Predicate called with the raised exception.
            Returns ``True`` to retry, ``False`` to propagate
            immediately.
        max_attempts: Total attempts including the first.  Must be
            ``>= 1``.
        base: Base delay (seconds) for the first retry.  Set to
            ``0.0`` to disable backoff (useful for self-correction
            loops where the next attempt sends a richer payload).
        cap: Maximum delay between any two attempts.
        event: Structured log event name emitted on each retry.
        jitter: If True, sleep for a uniform random duration in
            ``[0, computed_delay]``; otherwise sleep for the full
            ``computed_delay``.

    Raises:
        ValueError: If ``max_attempts < 1`` or ``base < 0`` or
            ``cap < base``.
    """

    def __init__(  # noqa: PLR0913 -- 6 named params is the irreducible config surface
        self,
        *,
        retryable: Callable[[BaseException], bool],
        max_attempts: int,
        base: float,
        cap: float,
        event: str,
        jitter: bool = True,
    ) -> None:
        if max_attempts < 1:
            msg = f"max_attempts must be >= 1, got {max_attempts}"
            raise ValueError(msg)
        if base < 0:
            msg = f"base must be >= 0, got {base}"
            raise ValueError(msg)
        if cap < base:
            msg = f"cap ({cap}) must be >= base ({base})"
            raise ValueError(msg)
        self._retryable = retryable
        self._max_attempts = max_attempts
        self._base = base
        self._cap = cap
        self._event = event
        self._jitter = jitter

    @property
    def max_attempts(self) -> int:
        """Maximum attempts (read by call sites that need the bound)."""
        return self._max_attempts

    async def execute(
        self,
        op: Callable[[], Awaitable[T]],
        **log_ctx: object,
    ) -> T:
        """Run ``op`` with retry; structured logs carry ``log_ctx``.

        ``op`` is called on every attempt.  If it raises and
        ``retryable(exc)`` is True and attempts remain, sleep then
        retry.  Otherwise the exception propagates with type
        intact (no wrapping).

        Returns the return value of the first successful attempt.
        """
        for attempt in range(self._max_attempts):
            try:
                return await op()
            except BaseException as exc:
                if not self._retryable(exc):
                    raise
                if attempt == self._max_attempts - 1:
                    raise
                delay = self._compute_delay(attempt)
                logger.warning(
                    self._event,
                    attempt=attempt + 1,
                    max_attempts=self._max_attempts,
                    backoff_seconds=delay,
                    error_type=type(exc).__name__,
                    **log_ctx,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
        msg = "GeneralRetryHandler exited the loop without raising or returning"
        raise AssertionError(msg)

    def _compute_delay(self, attempt: int) -> float:
        """Compute exponential-backoff delay for retry iteration ``attempt``.

        ``attempt`` is zero-based: 0 is the delay before the second
        attempt.  Returns ``0.0`` immediately if ``base == 0`` to
        cover self-correction loops that don't sleep between attempts.
        """
        if self._base == 0:
            return 0.0
        delay: float = min(self._base * (2**attempt), self._cap)
        if self._jitter:
            return float(random.uniform(0, delay))  # noqa: S311
        return delay
