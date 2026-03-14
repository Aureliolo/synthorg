"""Tests for delegation rate limiter."""

import pytest

from synthorg.communication.config import RateLimitConfig
from synthorg.communication.loop_prevention.rate_limit import (
    DelegationRateLimiter,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestDelegationRateLimiter:
    def test_first_check_passes(self) -> None:
        config = RateLimitConfig(max_per_pair_per_minute=3, burst_allowance=1)
        limiter = DelegationRateLimiter(config)
        result = limiter.check("a", "b")
        assert result.passed is True
        assert result.mechanism == "rate_limit"

    def test_within_limit_passes(self) -> None:
        config = RateLimitConfig(max_per_pair_per_minute=3, burst_allowance=1)
        clock_time = 100.0

        def clock() -> float:
            return clock_time

        limiter = DelegationRateLimiter(config, clock=clock)
        # Record 3 delegations (limit = 3 + 1 = 4)
        for _ in range(3):
            limiter.record("a", "b")
        result = limiter.check("a", "b")
        assert result.passed is True

    def test_exceeds_limit_fails(self) -> None:
        config = RateLimitConfig(max_per_pair_per_minute=3, burst_allowance=1)
        clock_time = 100.0

        def clock() -> float:
            return clock_time

        limiter = DelegationRateLimiter(config, clock=clock)
        # Record 4 delegations (at limit = 3 + 1 = 4)
        for _ in range(4):
            limiter.record("a", "b")
        result = limiter.check("a", "b")
        assert result.passed is False
        assert result.mechanism == "rate_limit"

    def test_expired_entries_pruned(self) -> None:
        config = RateLimitConfig(max_per_pair_per_minute=2, burst_allowance=0)
        clock_time = 100.0

        def clock() -> float:
            return clock_time

        limiter = DelegationRateLimiter(config, clock=clock)
        limiter.record("a", "b")
        limiter.record("a", "b")
        # At this point, limit reached (2 of 2)
        result = limiter.check("a", "b")
        assert result.passed is False

        # Advance clock past 60s window
        clock_time = 161.0
        result = limiter.check("a", "b")
        assert result.passed is True

    def test_sorted_pair_key(self) -> None:
        """(a,b) and (b,a) share the same rate limit."""
        config = RateLimitConfig(max_per_pair_per_minute=2, burst_allowance=0)
        clock_time = 100.0

        def clock() -> float:
            return clock_time

        limiter = DelegationRateLimiter(config, clock=clock)
        limiter.record("b", "a")  # reversed order
        limiter.record("a", "b")  # normal order
        result = limiter.check("a", "b")
        assert result.passed is False

    def test_different_pair_independent(self) -> None:
        config = RateLimitConfig(max_per_pair_per_minute=1, burst_allowance=0)
        clock_time = 100.0

        def clock() -> float:
            return clock_time

        limiter = DelegationRateLimiter(config, clock=clock)
        limiter.record("a", "b")
        # Pair (a,b) is at limit
        result = limiter.check("a", "b")
        assert result.passed is False
        # Pair (a,c) is unaffected
        result = limiter.check("a", "c")
        assert result.passed is True
