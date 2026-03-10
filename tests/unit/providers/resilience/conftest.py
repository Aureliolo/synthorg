"""Shared fixtures for resilience tests."""

import pytest

from ai_company.core.resilience_config import RateLimiterConfig, RetryConfig
from ai_company.providers.errors import (
    AuthenticationError,
    ProviderConnectionError,
    ProviderInternalError,
    ProviderTimeoutError,
    RateLimitError,
)


@pytest.fixture
def default_retry_config() -> RetryConfig:
    """RetryConfig with defaults."""
    return RetryConfig()


@pytest.fixture
def no_jitter_retry_config() -> RetryConfig:
    """RetryConfig with jitter disabled for deterministic tests."""
    return RetryConfig(jitter=False, base_delay=0.01, max_delay=1.0)


@pytest.fixture
def disabled_retry_config() -> RetryConfig:
    """RetryConfig with retries disabled."""
    return RetryConfig(max_retries=0)


@pytest.fixture
def default_rate_limiter_config() -> RateLimiterConfig:
    """RateLimiterConfig with defaults (unlimited)."""
    return RateLimiterConfig()


@pytest.fixture
def retryable_error() -> RateLimitError:
    """A retryable RateLimitError."""
    return RateLimitError("rate limited")


@pytest.fixture
def retryable_error_with_retry_after() -> RateLimitError:
    """A retryable RateLimitError with retry_after."""
    return RateLimitError("rate limited", retry_after=2.5)


@pytest.fixture
def non_retryable_error() -> AuthenticationError:
    """A non-retryable AuthenticationError."""
    return AuthenticationError("bad key")


@pytest.fixture
def timeout_error() -> ProviderTimeoutError:
    """A retryable ProviderTimeoutError."""
    return ProviderTimeoutError("timed out")


@pytest.fixture
def connection_error() -> ProviderConnectionError:
    """A retryable ProviderConnectionError."""
    return ProviderConnectionError("connection failed")


@pytest.fixture
def internal_error() -> ProviderInternalError:
    """A retryable ProviderInternalError."""
    return ProviderInternalError("server error")
