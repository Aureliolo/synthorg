"""Tests for delegation circuit breaker."""

import threading
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.communication.config import CircuitBreakerConfig
from synthorg.communication.loop_prevention.circuit_breaker import (
    CircuitBreakerState,
    DelegationCircuitBreaker,
)


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


@pytest.mark.unit
class TestCircuitBreakerExponentialBackoff:
    def test_first_trip_uses_base_cooldown(self) -> None:
        """First trip cooldown = base (300s)."""
        config = CircuitBreakerConfig(bounce_threshold=3, cooldown_seconds=300)
        clock_time = 100.0

        def clock() -> float:
            return clock_time

        cb = DelegationCircuitBreaker(config, clock=clock)
        for _ in range(3):
            cb.record_delegation("a", "b")
        assert cb.get_state("a", "b") is CircuitBreakerState.OPEN

        # At 399s (299s elapsed), still open
        clock_time = 399.0
        assert cb.get_state("a", "b") is CircuitBreakerState.OPEN

        # At 401s (301s elapsed), closed
        clock_time = 401.0
        assert cb.get_state("a", "b") is CircuitBreakerState.CLOSED

    def test_second_trip_doubles_cooldown(self) -> None:
        """Second trip cooldown = 600s (base * 2)."""
        config = CircuitBreakerConfig(bounce_threshold=3, cooldown_seconds=300)
        clock_time = 100.0

        def clock() -> float:
            return clock_time

        cb = DelegationCircuitBreaker(config, clock=clock)

        # Trip 1
        for _ in range(3):
            cb.record_delegation("a", "b")
        assert cb.get_state("a", "b") is CircuitBreakerState.OPEN

        # Cooldown expires (300s for trip 1)
        clock_time = 401.0
        assert cb.get_state("a", "b") is CircuitBreakerState.CLOSED

        # Trip 2
        for _ in range(3):
            cb.record_delegation("a", "b")
        assert cb.get_state("a", "b") is CircuitBreakerState.OPEN

        # At 401 + 599 = 1000 (599s into 600s cooldown), still open
        clock_time = 1000.0
        assert cb.get_state("a", "b") is CircuitBreakerState.OPEN

        # At 401 + 601 = 1002 (601s elapsed), closed
        clock_time = 1002.0
        assert cb.get_state("a", "b") is CircuitBreakerState.CLOSED

    def test_backoff_capped_at_max(self) -> None:
        """Cooldown is capped at max_cooldown_seconds."""
        config = CircuitBreakerConfig(
            bounce_threshold=1,
            cooldown_seconds=300,
            max_cooldown_seconds=1000,
        )
        clock_time = 0.0

        def clock() -> float:
            return clock_time

        cb = DelegationCircuitBreaker(config, clock=clock)

        # Trip 5 times: cooldown would be 300*2^4 = 4800 but capped at 1000
        for trip in range(5):
            cb.record_delegation("a", "b")
            # Advance past the current cooldown to reset
            cooldown = min(300 * 2**trip, 1000)
            clock_time += cooldown + 1
            cb.get_state("a", "b")  # triggers reset

        # 6th trip: cooldown should be 1000 (capped)
        cb.record_delegation("a", "b")
        open_time = clock_time

        # Still open at open_time + 999
        clock_time = open_time + 999
        assert cb.get_state("a", "b") is CircuitBreakerState.OPEN

        # Closed at open_time + 1001
        clock_time = open_time + 1001
        assert cb.get_state("a", "b") is CircuitBreakerState.CLOSED

    def test_trip_count_persists_after_reset(self) -> None:
        """Trip count survives cooldown resets, causing longer cooldowns."""
        config = CircuitBreakerConfig(bounce_threshold=2, cooldown_seconds=100)
        clock_time = 0.0

        def clock() -> float:
            return clock_time

        cb = DelegationCircuitBreaker(config, clock=clock)

        # Trip 1: cooldown = 100
        cb.record_delegation("a", "b")
        cb.record_delegation("a", "b")
        clock_time = 101.0
        assert cb.get_state("a", "b") is CircuitBreakerState.CLOSED

        # Trip 2: cooldown = 200
        cb.record_delegation("a", "b")
        cb.record_delegation("a", "b")
        clock_time = 101.0 + 199.0  # not enough
        assert cb.get_state("a", "b") is CircuitBreakerState.OPEN
        clock_time = 101.0 + 201.0  # enough
        assert cb.get_state("a", "b") is CircuitBreakerState.CLOSED

        # Trip 3: cooldown = 400
        cb.record_delegation("a", "b")
        cb.record_delegation("a", "b")
        clock_time = 302.0 + 399.0
        assert cb.get_state("a", "b") is CircuitBreakerState.OPEN
        clock_time = 302.0 + 401.0
        assert cb.get_state("a", "b") is CircuitBreakerState.CLOSED

    def test_independent_trip_counts_per_pair(self) -> None:
        """Different pairs have independent trip counts."""
        config = CircuitBreakerConfig(bounce_threshold=1, cooldown_seconds=100)
        clock_time = 0.0

        def clock() -> float:
            return clock_time

        cb = DelegationCircuitBreaker(config, clock=clock)

        # Trip (a,b) twice
        cb.record_delegation("a", "b")
        clock_time = 101.0
        cb.get_state("a", "b")  # reset
        cb.record_delegation("a", "b")

        # Trip (a,c) once
        cb.record_delegation("a", "c")

        # (a,b) trip_count=2 -> cooldown=200
        clock_time = 101.0 + 199.0
        assert cb.get_state("a", "b") is CircuitBreakerState.OPEN

        # (a,c) trip_count=1 -> cooldown=100
        clock_time = 101.0 + 101.0
        assert cb.get_state("a", "c") is CircuitBreakerState.CLOSED


@pytest.mark.unit
class TestCircuitBreakerDirtyTracking:
    def test_record_delegation_marks_dirty_on_trip(self) -> None:
        config = CircuitBreakerConfig(bounce_threshold=2, cooldown_seconds=300)
        cb = DelegationCircuitBreaker(config)
        cb.record_delegation("a", "b")
        assert ("a", "b") not in cb._dirty
        cb.record_delegation("a", "b")
        assert ("a", "b") in cb._dirty

    def test_get_state_marks_dirty_on_reset(self) -> None:
        config = CircuitBreakerConfig(bounce_threshold=1, cooldown_seconds=10)
        clock_time = 0.0

        def clock() -> float:
            return clock_time

        cb = DelegationCircuitBreaker(config, clock=clock)
        cb.record_delegation("a", "b")
        cb._dirty.clear()

        clock_time = 11.0
        cb.get_state("a", "b")  # triggers reset
        assert ("a", "b") in cb._dirty

    async def test_persist_dirty_clears_set(self) -> None:
        config = CircuitBreakerConfig(bounce_threshold=1, cooldown_seconds=10)
        repo = MagicMock()
        repo.save = AsyncMock()
        cb = DelegationCircuitBreaker(config, state_repo=repo)
        cb.record_delegation("a", "b")
        assert cb._dirty

        await cb.persist_dirty()
        assert not cb._dirty
        repo.save.assert_awaited_once()

    async def test_load_state_restores_pairs(self) -> None:
        from synthorg.persistence.circuit_breaker_repo import (
            CircuitBreakerStateRecord,
        )

        config = CircuitBreakerConfig(bounce_threshold=3, cooldown_seconds=300)
        record = CircuitBreakerStateRecord(
            pair_key_a="a",
            pair_key_b="b",
            bounce_count=1,
            trip_count=2,
            opened_at=50.0,
        )
        repo = MagicMock()
        repo.load_all = AsyncMock(return_value=(record,))

        cb = DelegationCircuitBreaker(config, state_repo=repo)
        await cb.load_state()

        pair = cb._pairs.get(("a", "b"))
        assert pair is not None
        assert pair.bounce_count == 1
        assert pair.trip_count == 2
        assert pair.opened_at == 50.0


@pytest.mark.unit
class TestCheckAtomicity:
    """Regression coverage for the ``check()`` TOCTOU race.

    The previous implementation called ``get_state()`` (which released
    the lock on return), then re-acquired the pair via a second
    ``_get_pair`` lookup outside the lock to compute the cooldown for
    the OPEN-branch log message.  A concurrent ``record_delegation``
    on the same pair could mutate the dict between those reads,
    surfacing a stale cooldown value or a missing pair.
    """

    def test_check_open_branch_runs_under_state_lock(self) -> None:
        """The whole OPEN-branch decision (state + cooldown read)
        runs while holding ``_state_lock``."""
        config = CircuitBreakerConfig(bounce_threshold=1, cooldown_seconds=10)
        clock_time = 0.0

        def clock() -> float:
            return clock_time

        cb = DelegationCircuitBreaker(config, clock=clock)
        cb.record_delegation("a", "b")

        # Wrap the lock so we can observe whether the protected
        # region was held across the OPEN-branch reads. Substituting
        # a tracking RLock keeps the API identical -- both
        # acquire/release pairs delegate to the underlying lock so
        # threading semantics are preserved.
        underlying = cb._state_lock
        acquired_during_check: list[bool] = []

        class _TrackingLock:
            def __enter__(self) -> _TrackingLock:
                underlying.acquire()
                acquired_during_check.append(True)
                return self

            def __exit__(
                self,
                exc_type: object,
                exc: object,
                tb: object,
            ) -> None:
                acquired_during_check.append(False)
                underlying.release()

            def acquire(self, *args: object, **kwargs: object) -> bool:
                return underlying.acquire()

            def release(self) -> None:
                underlying.release()

        cb._state_lock = _TrackingLock()  # type: ignore[assignment]
        result = cb.check("a", "b")
        assert result.passed is False
        # Exactly one acquire/release pair across the check, meaning
        # the entire OPEN branch decision was inside the critical
        # section (no second unlocked read).
        assert acquired_during_check == [True, False]

    def test_record_delegation_after_get_state_does_not_drop_pair(
        self,
    ) -> None:
        """A concurrent ``record_delegation`` between ``get_state`` and
        the cooldown read cannot leave ``check`` reading a missing pair.

        The fix folds both reads under one lock so the resetting
        branch in ``get_state`` and the OPEN-branch read in ``check``
        cannot interleave with a sibling mutation.
        """
        config = CircuitBreakerConfig(bounce_threshold=2, cooldown_seconds=10)
        clock_time = 0.0

        def clock() -> float:
            return clock_time

        cb = DelegationCircuitBreaker(config, clock=clock)
        cb.record_delegation("a", "b")
        cb.record_delegation("a", "b")
        # Pair is OPEN.  Wrap ``_state_lock`` with a tracking proxy
        # that fires a sibling thread's mutation while ``check``
        # holds the lock.  Under the fix, ``check`` reads
        # ``opened_at`` and ``trip_count`` while holding
        # ``_state_lock``; the sibling thread blocks on the lock
        # until ``check`` exits, so its mutation cannot influence
        # the OPEN-branch verdict.  Without the fix, the sibling
        # would race the post-``get_state`` re-read and the test
        # would observe ``passed=True``.
        from threading import Thread

        underlying = cb._state_lock
        injection_done = threading.Event()

        def _mutate_in_sibling() -> None:
            with underlying:
                pair = cb._pairs.get(("a", "b"))
                if pair is not None:
                    pair.opened_at = None
                injection_done.set()

        recorded_during_check: list[bool] = []

        class _TrackingLock:
            def __enter__(self) -> Any:
                result = underlying.__enter__()
                if not recorded_during_check:
                    recorded_during_check.append(True)
                    t = Thread(target=_mutate_in_sibling, daemon=True)
                    t.start()
                    # Yield to the sibling so it observes the lock
                    # held; it blocks on us until __exit__ fires.
                    time.sleep(0.05)
                return result

            def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
                underlying.__exit__(exc_type, exc, tb)

            def acquire(self, *args: Any, **kwargs: Any) -> Any:
                return underlying.acquire(*args, **kwargs)

            def release(self) -> None:
                underlying.release()

        cb._state_lock = _TrackingLock()  # type: ignore[assignment]
        clock_time = 5.0
        result = cb.check("a", "b")
        cb._state_lock = underlying  # type: ignore[assignment]
        injection_done.wait(timeout=1.0)
        assert recorded_during_check, (
            "check() never acquired _state_lock through the tracked "
            "wrapper; the OPEN-branch decision is NOT covered by "
            "the regression."
        )
        assert result.passed is False
        assert "cooldown" in (result.message or "")
