"""Unit tests for the gateway run-cost ledger's kill latch and eviction.

The kill latch is the load-bearing anti-bypass property: a run that crosses
its ceiling must stay rejected for the bearer's lifetime rather than being
zeroed and re-admitted on the next call.
"""

import pytest

from synthorg.api.gateway.ledger import RunCostLedger
from tests._shared import FakeClock

pytestmark = pytest.mark.unit


async def test_kill_pins_total_and_latches_without_zeroing() -> None:
    clock = FakeClock()
    ledger = RunCostLedger(clock=clock)
    await ledger.add("run-1", 1.0)

    await ledger.kill("run-1", 1.0)

    assert await ledger.is_killed("run-1") is True
    # The total is pinned (not reset to 0), so a reused bearer cannot respend.
    assert await ledger.total("run-1") == pytest.approx(1.0)


async def test_kill_pins_at_least_the_spent_floor() -> None:
    clock = FakeClock()
    ledger = RunCostLedger(clock=clock)
    await ledger.add("run-1", 0.2)

    # A larger crossing value is pinned; a smaller one never lowers the total.
    await ledger.kill("run-1", 0.9)
    assert await ledger.total("run-1") == pytest.approx(0.9)
    await ledger.kill("run-1", 0.1)
    assert await ledger.total("run-1") == pytest.approx(0.9)


async def test_is_killed_is_false_for_unseen_and_live_runs() -> None:
    clock = FakeClock()
    ledger = RunCostLedger(clock=clock)
    assert await ledger.is_killed("never-seen") is False
    await ledger.add("live", 0.5)
    assert await ledger.is_killed("live") is False


async def test_reset_clears_the_kill_latch() -> None:
    clock = FakeClock()
    ledger = RunCostLedger(clock=clock)
    await ledger.kill("run-1", 1.0)

    await ledger.reset("run-1")

    assert await ledger.is_killed("run-1") is False
    assert await ledger.total("run-1") == pytest.approx(0.0)


async def test_idle_killed_run_is_evicted_past_ttl() -> None:
    clock = FakeClock()
    ledger = RunCostLedger(clock=clock, entry_ttl_seconds=100.0)
    await ledger.kill("stale", 1.0)

    # Advance past the TTL, then any add triggers lazy idle eviction.
    clock.advance(101.0)
    await ledger.add("other", 0.1)

    # The idle killed entry is reclaimed (bearer could no longer be used).
    assert await ledger.is_killed("stale") is False
    assert await ledger.total("stale") == pytest.approx(0.0)


async def test_is_killed_check_keeps_active_run_from_eviction() -> None:
    clock = FakeClock()
    ledger = RunCostLedger(clock=clock, entry_ttl_seconds=100.0)
    await ledger.kill("active", 1.0)

    # A retry within each window re-touches the entry, so it survives.
    clock.advance(80.0)
    assert await ledger.is_killed("active") is True
    clock.advance(80.0)
    await ledger.add("other", 0.1)  # triggers eviction sweep

    # Total idle would exceed the TTL, but the mid-window check kept it live.
    assert await ledger.is_killed("active") is True
