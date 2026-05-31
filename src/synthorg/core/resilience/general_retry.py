"""General-purpose async retry handler for non-provider resilience.

See ``docs/reference/retry-patterns.md`` for the four-cell decision
tree spanning all retry families in the codebase. This helper
implements **Pattern A** (transient I/O with exponential backoff);
Patterns B and C live as inline loops at their callsites because
their semantics do not fit the temporal-backoff model.

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

- ``persistence.postgres.decision._cas`` retries
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

import math
import random
from typing import TYPE_CHECKING, Final, TypeVar

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.events.resilience import (
    CORE_RESILIENCE_INVALID_CONFIG,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger(__name__)

T = TypeVar("T")

# Reserved log-record kwargs that ``execute()`` injects on every retry
# attempt.  ``log_ctx`` keys are renamed with the ``ctx_`` prefix on
# collision so caller-supplied context cannot silently overwrite the
# handler's own diagnostic fields.
_RESERVED_LOG_KWARGS: Final[frozenset[str]] = frozenset(
    {
        "attempt",
        "max_attempts",
        "backoff_seconds",
        "error_type",
        "retry_decision",
    }
)


class GeneralRetryHandler:
    """Wrap an async callable with bounded retry + exponential backoff.

    Args:
        retryable: Predicate called with the raised exception.
            Returns ``True`` to retry, ``False`` to propagate
            immediately.
        max_attempts: Total attempts including the first.  Must be
            ``>= 1``.
        base: Base delay (seconds) for the first retry.  ``0.0``
            disables temporal backoff (every retry is immediate); this
            is rare in practice -- LLM self-correction loops use a
            different helper entirely (see the carve-out list at the
            top of this module), so the canonical use of ``base=0``
            is "I want bounded retry without a sleep budget" rather
            than "I'm building a self-correction loop".
        cap: Maximum delay between any two attempts.
        event: Structured log event name emitted on each retry.
        jitter: If True, sleep for a uniform random duration in
            ``[0, computed_delay]``; otherwise sleep for the full
            ``computed_delay``.

    Raises:
        ValueError: If ``max_attempts < 1``, ``base`` is non-finite
            (``NaN`` / ``+inf`` / ``-inf``) or negative, or ``cap`` is
            non-finite or smaller than ``base``.  Non-finite ``base``
            silently disables backoff (every ``base * 2 ** k`` is also
            non-finite) and a non-finite ``cap`` lets a single retry
            sleep effectively forever; both surface here.
    """

    def __init__(  # noqa: PLR0913 -- 7 named params is the irreducible config surface
        self,
        *,
        retryable: Callable[[Exception], bool],
        max_attempts: int,
        base: float,
        cap: float,
        event: str,
        jitter: bool = True,
        clock: Clock | None = None,
    ) -> None:
        if max_attempts < 1:
            msg = f"max_attempts must be >= 1, got {max_attempts}"
            logger.error(
                CORE_RESILIENCE_INVALID_CONFIG,
                retry_event=event,
                parameter="max_attempts",
                value=max_attempts,
                reason=msg,
            )
            raise ValueError(msg)
        # Reject ``NaN`` / ``+inf`` / ``-inf``: a non-finite ``base``
        # silently disables backoff (every ``base * 2 ** k`` is also
        # non-finite, ``min(...)`` collapses to ``cap``) and a non-finite
        # ``cap`` lets a single retry sleep effectively forever.  Both
        # are operator misconfig and surface here.
        if not math.isfinite(base) or base < 0:
            msg = f"base must be a finite number >= 0, got {base!r}"
            logger.error(
                CORE_RESILIENCE_INVALID_CONFIG,
                retry_event=event,
                parameter="base",
                value=base,
                reason=msg,
            )
            raise ValueError(msg)
        if not math.isfinite(cap) or cap < base:
            msg = f"cap must be a finite number >= base ({base}), got {cap!r}"
            logger.error(
                CORE_RESILIENCE_INVALID_CONFIG,
                retry_event=event,
                parameter="cap",
                value=cap,
                base=base,
                reason=msg,
            )
            raise ValueError(msg)
        self._retryable = retryable
        self._max_attempts = max_attempts
        self._base = base
        self._cap = cap
        self._event = event
        self._jitter = jitter
        self._clock: Clock = clock if clock is not None else SystemClock()

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
        intact (no wrapping).  ``except Exception`` (NOT
        ``BaseException``) so ``asyncio.CancelledError``,
        ``KeyboardInterrupt``, and ``SystemExit`` propagate
        immediately without being run through ``self._retryable``.

        Returns:
            The return value of the first successful ``op`` attempt.

        Raises:
            AssertionError: If the retry loop exits without returning or
                raising (an unreachable-state guard).
        """
        safe_ctx = self._safe_log_ctx(log_ctx)
        for attempt in range(self._max_attempts):
            try:
                return await op()
            except Exception as exc:
                reraise_critical(exc)
                if not self._retryable(exc):
                    self._log_attempt(
                        attempt,
                        0.0,
                        "not_retryable",
                        exc,
                        safe_ctx,
                    )
                    raise
                if attempt == self._max_attempts - 1:
                    self._log_attempt(
                        attempt,
                        0.0,
                        "exhausted",
                        exc,
                        safe_ctx,
                    )
                    raise
                delay = self._compute_delay(attempt)
                self._log_attempt(attempt, delay, "retrying", exc, safe_ctx)
                if delay > 0:
                    await self._clock.sleep(delay)
        msg = "GeneralRetryHandler exited the loop without raising or returning"
        raise AssertionError(msg)

    @staticmethod
    def _safe_log_ctx(log_ctx: dict[str, object]) -> dict[str, object]:
        """Rename caller keys that collide with handler-injected fields.

        Reserved keys (``attempt`` / ``max_attempts`` / ``backoff_seconds``
        / ``error_type`` / ``retry_decision``) get a ``ctx_`` prefix so
        the handler's own diagnostic fields cannot be silently
        overwritten in the structured log record.

        Returns:
            A copy of *log_ctx* with any reserved keys renamed under a
            ``ctx_`` prefix.
        """
        return {
            (f"ctx_{k}" if k in _RESERVED_LOG_KWARGS else k): v
            for k, v in log_ctx.items()
        }

    def _log_attempt(
        self,
        attempt: int,
        backoff_seconds: float,
        retry_decision: str,
        exc: BaseException,
        safe_ctx: dict[str, object],
    ) -> None:
        """Emit one structured retry-attempt warning with shared shape.

        Centralised so the three call sites in :meth:`execute` carry
        identical telemetry shape; downstream dashboards can chart
        ``retry_decision`` partitions without per-call-site drift.
        """
        logger.warning(
            self._event,
            attempt=attempt + 1,
            max_attempts=self._max_attempts,
            backoff_seconds=backoff_seconds,
            error_type=type(exc).__name__,
            retry_decision=retry_decision,
            **safe_ctx,
        )

    def _compute_delay(self, attempt: int) -> float:
        """Compute exponential-backoff delay for retry iteration ``attempt``.

        ``attempt`` is zero-based: 0 is the delay before the second
        attempt.  Returns ``0.0`` when ``base == 0`` so callers can
        opt into immediate retries without sleeping; LLM self-correction
        loops are an explicit non-goal of this helper (see the carve-out
        list at the top of the module).

        Returns:
            The backoff delay in seconds (exponential, capped, optional
            jitter), or ``0.0`` when the configured base is ``0``.
        """
        if self._base == 0:
            return 0.0
        delay: float = min(self._base * (2**attempt), self._cap)
        if self._jitter:
            return float(random.uniform(0, delay))  # noqa: S311
        return delay
