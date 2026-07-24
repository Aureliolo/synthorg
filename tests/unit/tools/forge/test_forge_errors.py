"""Tests for the forge tool error hierarchy's retryability classification."""

import pytest

from synthorg.tools.forge.errors import (
    ForgeRateLimitedError,
    ForgeUpstreamApiError,
    ForgeUpstreamAuthError,
    ForgeUpstreamError,
)

pytestmark = pytest.mark.unit


class TestForgeErrorRetryability:
    def test_rate_limited_is_retryable(self) -> None:
        # A 429 is transient by definition; matches every sibling
        # RateLimitError leaf.
        assert ForgeRateLimitedError.retryable is True

    def test_rate_limited_carries_retry_after(self) -> None:
        err = ForgeRateLimitedError("slow", retry_after_seconds=12.0)
        assert err.retry_after_seconds == 12.0

    def test_upstream_auth_is_not_retryable(self) -> None:
        # A permanent auth failure must not be retried.
        assert ForgeUpstreamAuthError.retryable is False

    def test_upstream_api_is_retryable(self) -> None:
        # A transient 5xx / transport failure is retryable.
        assert ForgeUpstreamApiError.retryable is True

    def test_upstream_leaves_share_the_base(self) -> None:
        assert issubclass(ForgeUpstreamAuthError, ForgeUpstreamError)
        assert issubclass(ForgeUpstreamApiError, ForgeUpstreamError)
