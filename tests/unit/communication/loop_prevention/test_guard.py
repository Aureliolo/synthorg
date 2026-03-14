"""Tests for DelegationGuard orchestrator."""

import pytest

from synthorg.communication.config import (
    CircuitBreakerConfig,
    LoopPreventionConfig,
    RateLimitConfig,
)
from synthorg.communication.loop_prevention.guard import (
    DelegationGuard,
)

pytestmark = pytest.mark.timeout(30)


def _make_config(**overrides: object) -> LoopPreventionConfig:
    """Create a LoopPreventionConfig with test-friendly defaults."""
    defaults: dict[str, object] = {
        "max_delegation_depth": 5,
        "dedup_window_seconds": 60,
        "rate_limit": RateLimitConfig(max_per_pair_per_minute=10, burst_allowance=3),
        "circuit_breaker": CircuitBreakerConfig(
            bounce_threshold=3, cooldown_seconds=300
        ),
    }
    defaults.update(overrides)
    return LoopPreventionConfig(**defaults)  # type: ignore[arg-type]


@pytest.mark.unit
class TestDelegationGuard:
    def test_all_checks_pass(self) -> None:
        guard = DelegationGuard(_make_config())
        result = guard.check(
            delegation_chain=("ceo",),
            delegator_id="ceo",
            delegatee_id="cto",
            task_id="Build feature X",
        )
        assert result.passed is True
        assert result.mechanism == "all_passed"

    def test_ancestry_blocked(self) -> None:
        guard = DelegationGuard(_make_config())
        result = guard.check(
            delegation_chain=("ceo", "cto"),
            delegator_id="cto",
            delegatee_id="ceo",
            task_id="Build feature X",
        )
        assert result.passed is False
        assert result.mechanism == "ancestry"

    def test_depth_exceeded(self) -> None:
        guard = DelegationGuard(_make_config(max_delegation_depth=2))
        result = guard.check(
            delegation_chain=("a", "b"),
            delegator_id="b",
            delegatee_id="c",
            task_id="Task",
        )
        assert result.passed is False
        assert result.mechanism == "max_depth"

    def test_dedup_blocked(self) -> None:
        guard = DelegationGuard(_make_config())
        # First delegation succeeds
        result = guard.check((), "a", "b", "Task")
        assert result.passed is True
        guard.record_delegation("a", "b", "Task")
        # Same delegation again
        result = guard.check((), "a", "b", "Task")
        assert result.passed is False
        assert result.mechanism == "dedup"

    def test_rate_limit_blocked(self) -> None:
        config = _make_config(
            rate_limit=RateLimitConfig(max_per_pair_per_minute=2, burst_allowance=0),
        )
        guard = DelegationGuard(config)
        for i in range(2):
            guard.record_delegation("a", "b", f"Task-{i}")
        result = guard.check((), "a", "b", "Task-new")
        assert result.passed is False
        assert result.mechanism == "rate_limit"

    def test_circuit_breaker_blocked(self) -> None:
        config = _make_config(
            circuit_breaker=CircuitBreakerConfig(
                bounce_threshold=2, cooldown_seconds=300
            ),
        )
        guard = DelegationGuard(config)
        for i in range(2):
            guard.record_delegation("a", "b", f"Task-{i}")
        # Circuit should be open after 2 bounces
        result = guard.check((), "a", "b", "Task-new-2")
        assert result.passed is False
        assert result.mechanism == "circuit_breaker"

    def test_record_delegation_records_all(self) -> None:
        guard = DelegationGuard(_make_config())
        guard.record_delegation("a", "b", "Task-1")
        # After recording, dedup should block same triple
        result = guard.check((), "a", "b", "Task-1")
        assert result.passed is False
        assert result.mechanism == "dedup"

    def test_check_order_ancestry_first(self) -> None:
        """Ancestry is checked before depth, so ancestry error wins."""
        config = _make_config(max_delegation_depth=1)
        guard = DelegationGuard(config)
        # Both ancestry and depth would fail; ancestry checked first
        result = guard.check(("a",), "a", "a", "Task")
        assert result.passed is False
        assert result.mechanism == "ancestry"
