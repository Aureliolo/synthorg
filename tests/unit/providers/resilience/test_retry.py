"""Tests for RetryHandler."""

from unittest.mock import AsyncMock

import pytest
import structlog

from ai_company.core.resilience_config import RetryConfig
from ai_company.observability.events.provider import (
    PROVIDER_CALL_ERROR,
    PROVIDER_RETRY_ATTEMPT,
    PROVIDER_RETRY_EXHAUSTED,
    PROVIDER_RETRY_SKIPPED,
)
from ai_company.providers.errors import (
    AuthenticationError,
    ProviderConnectionError,
    ProviderInternalError,
    ProviderTimeoutError,
    RateLimitError,
)
from ai_company.providers.resilience.errors import RetryExhaustedError
from ai_company.providers.resilience.retry import RetryHandler

pytestmark = pytest.mark.timeout(30)


def _fast_config(
    max_retries: int = 3,
    *,
    jitter: bool = False,
) -> RetryConfig:
    """Build a fast RetryConfig for tests."""
    return RetryConfig(
        max_retries=max_retries,
        base_delay=0.001,
        max_delay=0.01,
        jitter=jitter,
    )


@pytest.mark.unit
class TestRetryHandlerSuccess:
    async def test_returns_result_on_first_success(self) -> None:
        handler = RetryHandler(_fast_config())
        func = AsyncMock(return_value="ok")
        result = await handler.execute(func)
        assert result == "ok"
        func.assert_awaited_once()

    async def test_retries_then_succeeds(self) -> None:
        handler = RetryHandler(_fast_config(max_retries=3))
        func = AsyncMock(
            side_effect=[
                RateLimitError("limited"),
                ProviderTimeoutError("timeout"),
                "ok",
            ],
        )
        result = await handler.execute(func)
        assert result == "ok"
        assert func.await_count == 3

    async def test_internal_error_is_retried(self) -> None:
        handler = RetryHandler(_fast_config(max_retries=2))
        error = ProviderInternalError("server error")
        func = AsyncMock(side_effect=[error, "ok"])
        result = await handler.execute(func)
        assert result == "ok"
        assert func.await_count == 2


@pytest.mark.unit
class TestRetryHandlerExhaustion:
    async def test_raises_retry_exhausted_after_max(self) -> None:
        handler = RetryHandler(_fast_config(max_retries=2))
        error = RateLimitError("limited")
        func = AsyncMock(side_effect=error)
        with pytest.raises(RetryExhaustedError) as exc_info:
            await handler.execute(func)
        assert exc_info.value.original_error is error
        assert func.await_count == 3  # 1 initial + 2 retries

    async def test_exhausted_error_is_not_retryable(self) -> None:
        handler = RetryHandler(_fast_config(max_retries=1))
        func = AsyncMock(side_effect=RateLimitError("limited"))
        with pytest.raises(RetryExhaustedError) as exc_info:
            await handler.execute(func)
        assert exc_info.value.is_retryable is False

    async def test_carries_last_error(self) -> None:
        handler = RetryHandler(_fast_config(max_retries=2))
        errors = [
            RateLimitError("first"),
            ProviderTimeoutError("second"),
            ProviderConnectionError("third"),
        ]
        func = AsyncMock(side_effect=errors)
        with pytest.raises(RetryExhaustedError) as exc_info:
            await handler.execute(func)
        assert exc_info.value.original_error is errors[2]


@pytest.mark.unit
class TestRetryHandlerNonRetryable:
    async def test_non_retryable_raises_immediately(self) -> None:
        handler = RetryHandler(_fast_config(max_retries=3))
        error = AuthenticationError("bad key")
        func = AsyncMock(side_effect=error)
        with pytest.raises(AuthenticationError):
            await handler.execute(func)
        func.assert_awaited_once()

    async def test_non_retryable_after_retryable(self) -> None:
        handler = RetryHandler(_fast_config(max_retries=3))
        func = AsyncMock(
            side_effect=[
                RateLimitError("limited"),
                AuthenticationError("bad key"),
            ],
        )
        with pytest.raises(AuthenticationError):
            await handler.execute(func)
        assert func.await_count == 2


@pytest.mark.unit
class TestRetryHandlerDisabled:
    async def test_zero_retries_raises_immediately(self) -> None:
        handler = RetryHandler(_fast_config(max_retries=0))
        error = RateLimitError("limited")
        func = AsyncMock(side_effect=error)
        with pytest.raises(RetryExhaustedError) as exc_info:
            await handler.execute(func)
        assert exc_info.value.original_error is error
        func.assert_awaited_once()

    async def test_zero_retries_non_retryable_raises_unwrapped(self) -> None:
        handler = RetryHandler(_fast_config(max_retries=0))
        from ai_company.providers.errors import AuthenticationError

        error = AuthenticationError("bad key")
        func = AsyncMock(side_effect=error)
        with pytest.raises(AuthenticationError):
            await handler.execute(func)
        func.assert_awaited_once()


@pytest.mark.unit
class TestRetryHandlerBackoff:
    def test_backoff_without_jitter(self) -> None:
        config = RetryConfig(
            max_retries=3,
            base_delay=1.0,
            max_delay=100.0,
            exponential_base=2.0,
            jitter=False,
        )
        handler = RetryHandler(config)
        error = RateLimitError("limited")

        # attempt 0: 1.0 * 2^0 = 1.0
        assert handler._compute_delay(0, error) == 1.0
        # attempt 1: 1.0 * 2^1 = 2.0
        assert handler._compute_delay(1, error) == 2.0
        # attempt 2: 1.0 * 2^2 = 4.0
        assert handler._compute_delay(2, error) == 4.0

    def test_backoff_capped_by_max_delay(self) -> None:
        config = RetryConfig(
            max_retries=10,
            base_delay=1.0,
            max_delay=5.0,
            exponential_base=2.0,
            jitter=False,
        )
        handler = RetryHandler(config)
        error = RateLimitError("limited")

        # attempt 5: 1.0 * 2^5 = 32.0, capped to 5.0
        assert handler._compute_delay(5, error) == 5.0

    def test_jitter_produces_bounded_values(self) -> None:
        config = RetryConfig(
            max_retries=3,
            base_delay=1.0,
            max_delay=100.0,
            exponential_base=2.0,
            jitter=True,
        )
        handler = RetryHandler(config)
        error = RateLimitError("limited")

        for _ in range(50):
            delay = handler._compute_delay(0, error)
            assert 0.0 <= delay <= 1.0  # base_delay * 2^0 = 1.0

    def test_retry_after_respected(self) -> None:
        config = RetryConfig(
            max_retries=3,
            base_delay=1.0,
            max_delay=100.0,
            exponential_base=2.0,
            jitter=False,
        )
        handler = RetryHandler(config)
        error = RateLimitError("limited", retry_after=5.0)

        delay = handler._compute_delay(0, error)
        assert delay == 5.0

    def test_retry_after_capped_by_max_delay(self) -> None:
        config = RetryConfig(
            max_retries=3,
            base_delay=1.0,
            max_delay=10.0,
            exponential_base=2.0,
            jitter=False,
        )
        handler = RetryHandler(config)
        error = RateLimitError("limited", retry_after=30.0)

        delay = handler._compute_delay(0, error)
        assert delay == 10.0


@pytest.mark.unit
class TestRetryHandlerLogging:
    async def test_logs_retry_attempt(self) -> None:
        handler = RetryHandler(_fast_config(max_retries=2))
        func = AsyncMock(
            side_effect=[RateLimitError("limited"), "ok"],
        )
        with structlog.testing.capture_logs() as cap:
            await handler.execute(func)
        attempts = [e for e in cap if e.get("event") == PROVIDER_RETRY_ATTEMPT]
        assert len(attempts) == 1
        assert attempts[0]["attempt"] == 1

    async def test_logs_retry_exhausted(self) -> None:
        handler = RetryHandler(_fast_config(max_retries=1))
        func = AsyncMock(side_effect=RateLimitError("limited"))
        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(RetryExhaustedError),
        ):
            await handler.execute(func)
        exhausted = [e for e in cap if e.get("event") == PROVIDER_RETRY_EXHAUSTED]
        assert len(exhausted) == 1

    async def test_logs_non_retryable_skip(self) -> None:
        handler = RetryHandler(_fast_config(max_retries=3))
        func = AsyncMock(side_effect=AuthenticationError("bad key"))
        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(AuthenticationError),
        ):
            await handler.execute(func)
        skipped = [e for e in cap if e.get("event") == PROVIDER_RETRY_SKIPPED]
        assert len(skipped) == 1


@pytest.mark.unit
class TestRetryHandlerNonProviderError:
    """Non-ProviderError exceptions must raise immediately without retry."""

    async def test_type_error_raises_immediately(self) -> None:
        handler = RetryHandler(_fast_config(max_retries=3))
        func = AsyncMock(side_effect=TypeError("unexpected"))
        with pytest.raises(TypeError, match="unexpected"):
            await handler.execute(func)
        func.assert_awaited_once()

    async def test_runtime_error_raises_immediately(self) -> None:
        handler = RetryHandler(_fast_config(max_retries=3))
        func = AsyncMock(side_effect=RuntimeError("bug"))
        with pytest.raises(RuntimeError, match="bug"):
            await handler.execute(func)
        func.assert_awaited_once()

    async def test_non_provider_error_logs_warning(self) -> None:
        handler = RetryHandler(_fast_config(max_retries=3))
        func = AsyncMock(side_effect=ValueError("bad value"))
        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(ValueError, match="bad value"),
        ):
            await handler.execute(func)
        errors = [
            e
            for e in cap
            if e.get("event") == PROVIDER_CALL_ERROR
            and e.get("reason") == "unexpected_non_provider_error"
        ]
        assert len(errors) == 1
