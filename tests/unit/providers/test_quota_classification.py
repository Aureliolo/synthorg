"""Tests for upstream billing-condition classification (both non-retryable)."""

import pytest

from synthorg.providers import errors
from synthorg.providers.drivers.litellm_billing import (
    is_payment_required,
    is_quota_exhaustion,
)


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
class TestPaymentRequiredDetection:
    """A 402 is read off the upstream status, not off wording.

    Unlike a plan quota, which only ollama's message body reveals, an empty
    balance arrives with the HTTP status that means exactly that. Reading
    the status is both more reliable and provider-neutral.
    """

    def test_detected_from_upstream_status(self) -> None:
        exc = Exception("insufficient balance")
        exc.status_code = 402  # type: ignore[attr-defined]
        assert is_payment_required(exc) is True

    @pytest.mark.parametrize("status", [400, 429, 500, 502, 503])
    def test_other_statuses_not_flagged(self, status: int) -> None:
        exc = Exception("upstream said no")
        exc.status_code = status  # type: ignore[attr-defined]
        assert is_payment_required(exc) is False

    def test_absent_status_is_not_a_payment_condition(self) -> None:
        # Never guess: an exception carrying no upstream status says nothing
        # about billing, and inferring one from wording would misfile every
        # error whose text happens to mention payment.
        assert is_payment_required(Exception("payment required")) is False

    def test_bool_status_is_not_402(self) -> None:
        exc = Exception("truthy")
        exc.status_code = True  # type: ignore[attr-defined]
        assert is_payment_required(exc) is False


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
