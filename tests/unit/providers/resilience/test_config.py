"""Tests for resilience configuration models."""

import pytest
from pydantic import ValidationError

from ai_company.providers.resilience.config import RateLimiterConfig, RetryConfig

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestRetryConfig:
    def test_defaults(self) -> None:
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.base_delay == 1.0
        assert config.max_delay == 60.0
        assert config.exponential_base == 2.0
        assert config.jitter is True

    def test_custom_values(self) -> None:
        config = RetryConfig(
            max_retries=5,
            base_delay=0.5,
            max_delay=30.0,
            exponential_base=3.0,
            jitter=False,
        )
        assert config.max_retries == 5
        assert config.base_delay == 0.5
        assert config.max_delay == 30.0
        assert config.exponential_base == 3.0
        assert config.jitter is False

    def test_zero_retries_disables(self) -> None:
        config = RetryConfig(max_retries=0)
        assert config.max_retries == 0

    def test_max_retries_upper_bound(self) -> None:
        config = RetryConfig(max_retries=10)
        assert config.max_retries == 10

    def test_max_retries_exceeds_upper_bound(self) -> None:
        with pytest.raises(ValidationError, match="less than or equal to 10"):
            RetryConfig(max_retries=11)

    def test_negative_max_retries_rejected(self) -> None:
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            RetryConfig(max_retries=-1)

    def test_negative_base_delay_rejected(self) -> None:
        with pytest.raises(ValidationError, match="greater than 0"):
            RetryConfig(base_delay=-1.0)

    def test_zero_base_delay_rejected(self) -> None:
        with pytest.raises(ValidationError, match="greater than 0"):
            RetryConfig(base_delay=0.0)

    def test_exponential_base_must_exceed_one(self) -> None:
        with pytest.raises(ValidationError, match="greater than 1"):
            RetryConfig(exponential_base=1.0)

    def test_frozen(self) -> None:
        config = RetryConfig()
        with pytest.raises(ValidationError):
            config.max_retries = 5  # type: ignore[misc]


@pytest.mark.unit
class TestRateLimiterConfig:
    def test_defaults(self) -> None:
        config = RateLimiterConfig()
        assert config.max_requests_per_minute == 0
        assert config.max_concurrent == 0

    def test_custom_values(self) -> None:
        config = RateLimiterConfig(
            max_requests_per_minute=60,
            max_concurrent=10,
        )
        assert config.max_requests_per_minute == 60
        assert config.max_concurrent == 10

    def test_zero_means_unlimited(self) -> None:
        config = RateLimiterConfig(
            max_requests_per_minute=0,
            max_concurrent=0,
        )
        assert config.max_requests_per_minute == 0
        assert config.max_concurrent == 0

    def test_negative_rpm_rejected(self) -> None:
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            RateLimiterConfig(max_requests_per_minute=-1)

    def test_negative_concurrent_rejected(self) -> None:
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            RateLimiterConfig(max_concurrent=-1)

    def test_frozen(self) -> None:
        config = RateLimiterConfig()
        with pytest.raises(ValidationError):
            config.max_concurrent = 5  # type: ignore[misc]
