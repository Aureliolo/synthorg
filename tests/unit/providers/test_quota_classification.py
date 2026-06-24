"""Tests for ollama quota-exhaustion classification (non-retryable)."""

import pytest

from synthorg.providers import errors
from synthorg.providers.drivers.litellm_quota import is_quota_exhaustion


@pytest.mark.unit
class TestQuotaExhaustionDetection:
    @pytest.mark.parametrize(
        "message",
        [
            "You have exceeded your weekly usage limit",
            "Cloud quota exhausted for this period",
            "session limit reached",
            "Please upgrade your plan to continue",
        ],
    )
    def test_quota_messages_detected(self, message: str) -> None:
        assert is_quota_exhaustion(Exception(message)) is True

    @pytest.mark.parametrize(
        "message",
        [
            "rate limit exceeded, retry shortly",
            "too many requests",
            "temporary throttling",
        ],
    )
    def test_transient_rate_limits_not_flagged(self, message: str) -> None:
        assert is_quota_exhaustion(Exception(message)) is False


@pytest.mark.unit
class TestQuotaErrorContract:
    def test_quota_error_is_non_retryable(self) -> None:
        err = errors.ProviderQuotaExceededError("quota exhausted")
        assert err.is_retryable is False

    def test_rate_limit_remains_retryable(self) -> None:
        assert errors.RateLimitError("slow down").is_retryable is True

    def test_quota_error_inherits_rate_limited_code(self) -> None:
        # Inheritance alias: quota exhaustion is a form of rate limiting; clients
        # branch on is_retryable, not a distinct code.
        assert (
            errors.ProviderQuotaExceededError.error_code
            == errors.RateLimitError.error_code
        )
