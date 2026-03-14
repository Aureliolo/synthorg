"""Tests for delegation circuit breaker."""

import pytest

from synthorg.communication.config import CircuitBreakerConfig
from synthorg.communication.loop_prevention.circuit_breaker import (
    CircuitBreakerState,
    DelegationCircuitBreaker,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestDelegationCircuitBreaker:
    def test_initial_state_closed(self) -> None:
        config = CircuitBreakerConfig(bounce_threshold=3, cooldown_seconds=300)
        cb = DelegationCircuitBreaker(config)
        assert cb.get_state("a", "b") is CircuitBreakerState.CLOSED

    def test_check_passes_when_closed(self) -> None:
        config = CircuitBreakerConfig(bounce_threshold=3, cooldown_seconds=300)
        cb = DelegationCircuitBreaker(config)
        result = cb.check("a", "b")
        assert result.passed is True
        assert result.mechanism == "circuit_breaker"

    def test_opens_after_threshold(self) -> None:
        config = CircuitBreakerConfig(bounce_threshold=3, cooldown_seconds=300)
        clock_time = 100.0

        def clock() -> float:
            return clock_time

        cb = DelegationCircuitBreaker(config, clock=clock)
        for _ in range(3):
            cb.record_delegation("a", "b")
        assert cb.get_state("a", "b") is CircuitBreakerState.OPEN

    def test_check_fails_when_open(self) -> None:
        config = CircuitBreakerConfig(bounce_threshold=3, cooldown_seconds=300)
        clock_time = 100.0

        def clock() -> float:
            return clock_time

        cb = DelegationCircuitBreaker(config, clock=clock)
        for _ in range(3):
            cb.record_delegation("a", "b")
        result = cb.check("a", "b")
        assert result.passed is False
        assert result.mechanism == "circuit_breaker"

    def test_resets_after_cooldown(self) -> None:
        config = CircuitBreakerConfig(bounce_threshold=3, cooldown_seconds=300)
        clock_time = 100.0

        def clock() -> float:
            return clock_time

        cb = DelegationCircuitBreaker(config, clock=clock)
        for _ in range(3):
            cb.record_delegation("a", "b")
        assert cb.get_state("a", "b") is CircuitBreakerState.OPEN

        clock_time = 401.0  # 301s later
        assert cb.get_state("a", "b") is CircuitBreakerState.CLOSED
        result = cb.check("a", "b")
        assert result.passed is True

    def test_sorted_pair_key(self) -> None:
        """(a,b) and (b,a) share the same circuit breaker."""
        config = CircuitBreakerConfig(bounce_threshold=2, cooldown_seconds=60)
        clock_time = 100.0

        def clock() -> float:
            return clock_time

        cb = DelegationCircuitBreaker(config, clock=clock)
        cb.record_delegation("b", "a")
        cb.record_delegation("a", "b")
        assert cb.get_state("a", "b") is CircuitBreakerState.OPEN

    def test_below_threshold_stays_closed(self) -> None:
        config = CircuitBreakerConfig(bounce_threshold=3, cooldown_seconds=300)
        cb = DelegationCircuitBreaker(config)
        cb.record_delegation("a", "b")
        cb.record_delegation("a", "b")
        assert cb.get_state("a", "b") is CircuitBreakerState.CLOSED

    def test_different_pair_independent(self) -> None:
        config = CircuitBreakerConfig(bounce_threshold=2, cooldown_seconds=60)
        cb = DelegationCircuitBreaker(config)
        cb.record_delegation("a", "b")
        cb.record_delegation("a", "b")
        assert cb.get_state("a", "b") is CircuitBreakerState.OPEN
        assert cb.get_state("a", "c") is CircuitBreakerState.CLOSED

    def test_record_delegation_noop_when_open(self) -> None:
        """Recording while circuit is open does not affect the state."""
        config = CircuitBreakerConfig(bounce_threshold=2, cooldown_seconds=300)
        clock_time = 100.0

        def clock() -> float:
            return clock_time

        cb = DelegationCircuitBreaker(config, clock=clock)
        cb.record_delegation("a", "b")
        cb.record_delegation("a", "b")
        assert cb.get_state("a", "b") is CircuitBreakerState.OPEN
        # Recording while open is a no-op
        cb.record_delegation("a", "b")
        # Should still be open, cooldown hasn't changed
        assert cb.get_state("a", "b") is CircuitBreakerState.OPEN
