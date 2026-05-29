"""Retry handler with exponential backoff and jitter."""

import asyncio
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, TypeVar

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_CALL_ERROR,
    PROVIDER_RETRY_ATTEMPT,
    PROVIDER_RETRY_EXHAUSTED,
    PROVIDER_RETRY_SKIPPED,
)
from synthorg.providers.errors import ProviderError, RateLimitError

from .errors import RetryExhaustedError

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from synthorg.core.resilience_config import RetryConfig

logger = get_logger(__name__)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RetryResult(Generic[T]):  # noqa: UP046
    """Immutable result of a retry-wrapped execution.

    Returned by :meth:`RetryHandler.execute` so callers get
    per-invocation retry metadata without shared mutable state.

    Attributes:
        value: The return value of the wrapped callable.
        attempt_count: Number of attempts made (1 = no retry).
        retry_reason: Exception type name if a retry occurred.
    """

    value: T
    attempt_count: int
    retry_reason: str | None


class RetryHandler:
    """Wraps async callables with retry logic.

    Retries transient errors (``is_retryable=True``) using exponential
    backoff with optional jitter.  Non-retryable errors raise immediately.
    After exhausting ``max_retries``, raises ``RetryExhaustedError``.

    Args:
        config: Retry configuration.
    """

    def __init__(self, config: RetryConfig) -> None:
        self._config = config

    async def execute(
        self,
        func: Callable[[], Coroutine[object, object, T]],
    ) -> RetryResult[T]:
        """Execute *func* with retry on transient errors.

        Args:
            func: Zero-argument async callable to execute.

        Returns:
            A :class:`RetryResult` containing the return value and
            per-invocation retry metadata.

        Raises:
            RetryExhaustedError: If all retries are exhausted.
            ProviderError: If the error is non-retryable.
            RuntimeError: If the retry loop exits with no recorded error
                (an internal invariant violation).
        """
        attempt_count = 0
        retry_reason: str | None = None
        last_error: ProviderError | None = None

        for attempt in range(1 + self._config.max_retries):
            attempt_count = attempt + 1
            try:
                value = await func()
                return RetryResult(
                    value=value,
                    attempt_count=attempt_count,
                    retry_reason=retry_reason,
                )
            except ProviderError as exc:
                last_error = self._handle_retryable_error(exc)
                if last_error is None:
                    raise
                retry_reason = type(exc).__name__
                if attempt >= self._config.max_retries:
                    break
                delay = self._compute_delay(attempt, exc)
                logger.info(
                    PROVIDER_RETRY_ATTEMPT,
                    attempt=attempt + 1,
                    max_retries=self._config.max_retries,
                    delay=delay,
                    error_type=type(exc).__name__,
                )
                await asyncio.sleep(delay)
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    PROVIDER_CALL_ERROR,
                    reason="unexpected_non_provider_error",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise

        if last_error is None:
            msg = "RetryHandler reached exhaustion with no recorded error"
            raise RuntimeError(msg)
        logger.warning(
            PROVIDER_RETRY_EXHAUSTED,
            max_retries=self._config.max_retries,
            error_type=type(last_error).__name__,
        )
        raise RetryExhaustedError(last_error) from last_error

    def _handle_retryable_error(
        self,
        exc: ProviderError,
    ) -> ProviderError | None:
        """Classify a provider error for retry decisions.

        Returns the error if retryable (caller should continue retrying),
        or ``None`` if non-retryable (caller must re-raise immediately).

        Returns:
            The error when retryable, or ``None`` when non-retryable.
        """
        if not exc.is_retryable:
            logger.warning(
                PROVIDER_RETRY_SKIPPED,
                error_type=type(exc).__name__,
                reason="non_retryable",
            )
            return None
        return exc

    def _compute_delay(self, attempt: int, exc: ProviderError) -> float:
        """Compute delay for the given retry iteration.

        Respects ``RateLimitError.retry_after`` when available.  Otherwise
        uses exponential backoff with optional jitter.

        Args:
            attempt: Zero-based retry iteration counter (0 = first retry,
                1 = second retry, etc.).
            exc: The error that triggered the retry.

        Returns:
            Delay in seconds.
        """
        if isinstance(exc, RateLimitError) and exc.retry_after is not None:
            return min(exc.retry_after, self._config.max_delay)

        delay = self._config.base_delay * (self._config.exponential_base**attempt)
        delay = min(delay, self._config.max_delay)

        if self._config.jitter:
            delay = random.uniform(0, delay)  # noqa: S311

        return delay
