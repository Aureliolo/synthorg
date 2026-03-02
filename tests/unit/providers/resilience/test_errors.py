"""Tests for RetryExhaustedError."""

import pytest

from ai_company.providers.errors import ProviderError, RateLimitError
from ai_company.providers.resilience.errors import RetryExhaustedError

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestRetryExhaustedError:
    def test_is_provider_error_subclass(self) -> None:
        err = RetryExhaustedError(RateLimitError("rate limited"))
        assert isinstance(err, ProviderError)

    def test_is_not_retryable(self) -> None:
        err = RetryExhaustedError(RateLimitError("rate limited"))
        assert err.is_retryable is False

    def test_carries_original_error(self) -> None:
        original = RateLimitError("rate limited", retry_after=5.0)
        err = RetryExhaustedError(original)
        assert err.original_error is original

    def test_message_includes_original(self) -> None:
        original = RateLimitError("rate limited")
        err = RetryExhaustedError(original)
        assert "rate limited" in err.message

    def test_context_from_original(self) -> None:
        original = RateLimitError(
            "rate limited",
            context={"provider": "test-provider", "model": "test-model"},
        )
        err = RetryExhaustedError(original)
        assert err.context["provider"] == "test-provider"
        assert err.context["model"] == "test-model"

    def test_str_representation(self) -> None:
        original = RateLimitError("rate limited")
        err = RetryExhaustedError(original)
        assert "rate limited" in str(err)
