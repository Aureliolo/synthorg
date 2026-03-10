"""Retry handler with exponential backoff and jitter."""

import asyncio
import random
from typing import TYPE_CHECKING, TypeVar

from ai_company.observability import get_logger
from ai_company.observability.events.provider import (
    PROVIDER_CALL_ERROR,
    PROVIDER_RETRY_ATTEMPT,
    PROVIDER_RETRY_EXHAUSTED,
    PROVIDER_RETRY_SKIPPED,
)
from ai_company.providers.errors import ProviderError, RateLimitError

from .errors import RetryExhaustedError

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from ai_company.core.resilience_config import RetryConfig

logger = get_logger(__name__)

T = TypeVar("T")


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
    ) -> T:
        """Execute *func* with retry on transient errors.

        Args:
            func: Zero-argument async callable to execute.

        Returns:
            The return value of *func*.

        Raises:
            RetryExhaustedError: If all retries are exhausted.
            ProviderError: If the error is non-retryable.
        """
        last_error: ProviderError | None = None

        for attempt in range(1 + self._config.max_retries):
            try:
                return await func()
            except ProviderError as exc:
                last_error = self._handle_retryable_error(exc)
                if last_error is None:
                    raise
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
            except Exception:
                logger.warning(
                    PROVIDER_CALL_ERROR,
                    reason="unexpected_non_provider_error",
                    exc_info=True,
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
