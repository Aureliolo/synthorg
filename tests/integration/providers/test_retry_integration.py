"""Integration tests for retry + rate limiting with the LiteLLM driver.

Tests exercise the full stack: LiteLLMDriver → BaseCompletionProvider
resilience wiring → RetryHandler → RateLimiter, with LiteLLM mocked
at the ``litellm.acompletion`` boundary.
"""

from unittest.mock import AsyncMock, patch

import pytest

from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.core.resilience_config import RateLimiterConfig, RetryConfig
from synthorg.providers.drivers.litellm_driver import LiteLLMDriver
from synthorg.providers.enums import MessageRole
from synthorg.providers.errors import (
    AuthenticationError,
    ProviderTimeoutError,
    RateLimitError,
)
from synthorg.providers.models import ChatMessage
from synthorg.providers.resilience.errors import RetryExhaustedError

from .conftest import build_model_response

pytestmark = pytest.mark.timeout(30)

_PATCH_ACOMPLETION = "litellm.acompletion"


def _make_config(
    *,
    max_retries: int = 2,
    max_requests_per_minute: int = 0,
    max_concurrent: int = 0,
) -> ProviderConfig:
    return ProviderConfig(
        driver="litellm",
        api_key="sk-test-key",
        models=(
            ProviderModelConfig(
                id="test-model-001",
                alias="test-model",
                cost_per_1k_input=0.001,
                cost_per_1k_output=0.002,
            ),
        ),
        retry=RetryConfig(
            max_retries=max_retries,
            base_delay=0.001,
            max_delay=0.01,
            jitter=False,
        ),
        rate_limiter=RateLimiterConfig(
            max_requests_per_minute=max_requests_per_minute,
            max_concurrent=max_concurrent,
        ),
    )


def _make_driver(config: ProviderConfig | None = None) -> LiteLLMDriver:
    return LiteLLMDriver("test-provider", config or _make_config())


def _user_messages() -> list[ChatMessage]:
    return [ChatMessage(role=MessageRole.USER, content="Hello")]


@pytest.mark.integration
class TestRetryIntegration:
    """Retry handler wired into the LiteLLM driver."""

    async def test_succeeds_after_transient_failure(self) -> None:
        driver = _make_driver()
        import litellm as _litellm

        transient = _litellm.Timeout(  # type: ignore[attr-defined]
            message="Timeout",
            model="test-model-001",
            llm_provider="test-provider",
        )
        success = build_model_response(
            content="Recovered",
            model="test-model-001",
        )

        with patch(
            _PATCH_ACOMPLETION,
            new_callable=AsyncMock,
        ) as m:
            m.side_effect = [transient, success]
            result = await driver.complete(_user_messages(), "test-model")

        assert result.content == "Recovered"
        assert m.await_count == 2

    async def test_exhausts_retries_raises_retry_exhausted(self) -> None:
        driver = _make_driver()
        import litellm as _litellm

        transient = _litellm.Timeout(  # type: ignore[attr-defined]
            message="Timeout",
            model="test-model-001",
            llm_provider="test-provider",
        )

        with patch(
            _PATCH_ACOMPLETION,
            new_callable=AsyncMock,
        ) as m:
            m.side_effect = transient
            with pytest.raises(RetryExhaustedError) as exc_info:
                await driver.complete(_user_messages(), "test-model")

        assert isinstance(exc_info.value.original_error, ProviderTimeoutError)
        assert m.await_count == 3  # 1 initial + 2 retries

    async def test_non_retryable_not_retried(self) -> None:
        driver = _make_driver()
        import litellm as _litellm

        auth_err = _litellm.AuthenticationError(  # type: ignore[attr-defined]
            message="Invalid key",
            model="test-model-001",
            llm_provider="test-provider",
        )

        with patch(
            _PATCH_ACOMPLETION,
            new_callable=AsyncMock,
        ) as m:
            m.side_effect = auth_err
            with pytest.raises(AuthenticationError):
                await driver.complete(_user_messages(), "test-model")

        m.assert_awaited_once()

    async def test_rate_limit_with_retry_after_respected(self) -> None:
        driver = _make_driver()
        import litellm as _litellm

        rate_err = _litellm.RateLimitError(  # type: ignore[attr-defined]
            message="Rate limited",
            model="test-model-001",
            llm_provider="test-provider",
        )
        rate_err.headers = {"retry-after": "0.01"}  # type: ignore[attr-defined]

        success = build_model_response(
            content="After rate limit",
            model="test-model-001",
        )

        with patch(
            _PATCH_ACOMPLETION,
            new_callable=AsyncMock,
        ) as m:
            m.side_effect = [rate_err, success]
            result = await driver.complete(_user_messages(), "test-model")

        assert result.content == "After rate limit"
        assert m.await_count == 2

    async def test_stream_retries_connection_setup(self) -> None:
        driver = _make_driver()
        import litellm as _litellm

        transient = _litellm.APIConnectionError(  # type: ignore[attr-defined]
            message="Connection refused",
            model="test-model-001",
            llm_provider="test-provider",
        )

        with patch(
            _PATCH_ACOMPLETION,
            new_callable=AsyncMock,
        ) as m:
            m.side_effect = transient
            with pytest.raises(RetryExhaustedError):
                await driver.stream(_user_messages(), "test-model")

        # 1 initial + 2 retries = 3 total
        assert m.await_count == 3


@pytest.mark.integration
class TestStreamRetryIntegration:
    """Retry behaviour on the streaming path."""

    async def test_stream_succeeds_after_transient_connection_error(self) -> None:
        """Stream setup is retried on transient failure; success on second attempt."""
        from synthorg.providers.enums import StreamEventType

        driver = _make_driver()
        import litellm as _litellm

        transient = _litellm.APIConnectionError(  # type: ignore[attr-defined]
            message="Connection refused",
            model="test-model-001",
            llm_provider="test-provider",
        )

        async def _empty_stream() -> None:  # type: ignore[misc]
            # Async generator that yields nothing (makes it an async iterable)
            return
            yield  # pragma: no cover

        with patch(
            _PATCH_ACOMPLETION,
            new_callable=AsyncMock,
        ) as m:
            # First call raises; second returns an empty async stream.
            m.side_effect = [transient, _empty_stream()]  # type: ignore[func-returns-value]

            stream = await driver.stream(_user_messages(), "test-model")
            chunks = [c async for c in stream]

        assert m.await_count == 2
        # _wrap_stream always emits a DONE chunk at the end
        assert chunks[-1].event_type == StreamEventType.DONE


@pytest.mark.integration
class TestRetryDisabledIntegration:
    """When retries are disabled, errors pass through unchanged."""

    async def test_retryable_error_not_wrapped(self) -> None:
        config = _make_config(max_retries=0)
        driver = _make_driver(config)
        import litellm as _litellm

        timeout = _litellm.Timeout(  # type: ignore[attr-defined]
            message="Timeout",
            model="test-model-001",
            llm_provider="test-provider",
        )

        with patch(
            _PATCH_ACOMPLETION,
            new_callable=AsyncMock,
        ) as m:
            m.side_effect = timeout
            with pytest.raises(ProviderTimeoutError):
                await driver.complete(_user_messages(), "test-model")

        m.assert_awaited_once()

    async def test_rate_limit_passes_through(self) -> None:
        config = _make_config(max_retries=0)
        driver = _make_driver(config)
        import litellm as _litellm

        rate_err = _litellm.RateLimitError(  # type: ignore[attr-defined]
            message="Rate limited",
            model="test-model-001",
            llm_provider="test-provider",
        )
        rate_err.headers = {"retry-after": "5"}  # type: ignore[attr-defined]

        with patch(
            _PATCH_ACOMPLETION,
            new_callable=AsyncMock,
        ) as m:
            m.side_effect = rate_err
            with pytest.raises(RateLimitError) as exc_info:
                await driver.complete(_user_messages(), "test-model")

        assert exc_info.value.retry_after == 5.0
        m.assert_awaited_once()


@pytest.mark.integration
class TestRetryWithRateLimitIntegration:
    """Retry + rate limiting enabled together."""

    async def test_retry_with_concurrent_limit(self) -> None:
        """Retry correctly releases and re-acquires rate limiter slot."""
        config = _make_config(max_retries=1, max_concurrent=1)
        driver = _make_driver(config)
        import litellm as _litellm

        transient = _litellm.Timeout(  # type: ignore[attr-defined]
            message="Timeout",
            model="test-model-001",
            llm_provider="test-provider",
        )
        success = build_model_response(
            content="Recovered",
            model="test-model-001",
        )

        with patch(
            _PATCH_ACOMPLETION,
            new_callable=AsyncMock,
        ) as m:
            m.side_effect = [transient, success]
            result = await driver.complete(_user_messages(), "test-model")

        assert result.content == "Recovered"
        assert m.await_count == 2
