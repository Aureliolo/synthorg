"""Tests for delegation deduplicator."""

import pytest

from synthorg.communication.loop_prevention.dedup import (
    DelegationDeduplicator,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestDelegationDeduplicator:
    def test_first_check_passes(self) -> None:
        dedup = DelegationDeduplicator(window_seconds=60)
        result = dedup.check("a", "b", "task-1")
        assert result.passed is True
        assert result.mechanism == "dedup"

    def test_duplicate_within_window_fails(self) -> None:
        clock_time = 100.0

        def clock() -> float:
            return clock_time

        dedup = DelegationDeduplicator(window_seconds=60, clock=clock)
        dedup.record("a", "b", "task-1")
        clock_time = 130.0  # 30s later, within window
        result = dedup.check("a", "b", "task-1")
        assert result.passed is False
        assert result.mechanism == "dedup"

    def test_duplicate_after_window_passes(self) -> None:
        clock_time = 100.0

        def clock() -> float:
            return clock_time

        dedup = DelegationDeduplicator(window_seconds=60, clock=clock)
        dedup.record("a", "b", "task-1")
        clock_time = 161.0  # 61s later, outside window
        result = dedup.check("a", "b", "task-1")
        assert result.passed is True

    def test_different_task_id_passes(self) -> None:
        clock_time = 100.0

        def clock() -> float:
            return clock_time

        dedup = DelegationDeduplicator(window_seconds=60, clock=clock)
        dedup.record("a", "b", "task-1")
        result = dedup.check("a", "b", "task-2")
        assert result.passed is True

    def test_different_pair_passes(self) -> None:
        clock_time = 100.0

        def clock() -> float:
            return clock_time

        dedup = DelegationDeduplicator(window_seconds=60, clock=clock)
        dedup.record("a", "b", "task-1")
        result = dedup.check("a", "c", "task-1")
        assert result.passed is True

    def test_directional_key_a_to_b_distinct_from_b_to_a(self) -> None:
        """Dedup uses directional keys: A->B and B->A are distinct."""
        clock_time = 100.0

        def clock() -> float:
            return clock_time

        dedup = DelegationDeduplicator(window_seconds=60, clock=clock)
        dedup.record("a", "b", "task-1")
        # Reverse direction should pass (directional)
        result = dedup.check("b", "a", "task-1")
        assert result.passed is True

    def test_expired_entries_pruned_on_check(self) -> None:
        """Expired entries are removed when check detects them."""
        clock_time = 100.0

        def clock() -> float:
            return clock_time

        dedup = DelegationDeduplicator(window_seconds=60, clock=clock)
        dedup.record("a", "b", "task-1")
        clock_time = 161.0  # expired
        dedup.check("a", "b", "task-1")
        # Internal record should be pruned
        assert ("a", "b", "task-1") not in dedup._records

    def test_record_updates_timestamp(self) -> None:
        clock_time = 100.0

        def clock() -> float:
            return clock_time

        dedup = DelegationDeduplicator(window_seconds=60, clock=clock)
        dedup.record("a", "b", "task-1")
        clock_time = 150.0  # 50s later
        dedup.record("a", "b", "task-1")  # re-record
        clock_time = 200.0  # 100s after first, 50s after second
        result = dedup.check("a", "b", "task-1")
        assert result.passed is False  # still within window of 2nd

    def test_global_purge_removes_all_expired(self) -> None:
        """Multiple expired entries are pruned in a single sweep."""
        clock_time = 100.0

        def clock() -> float:
            return clock_time

        dedup = DelegationDeduplicator(window_seconds=60, clock=clock)
        dedup.record("a", "b", "task-1")
        dedup.record("c", "d", "task-2")
        dedup.record("e", "f", "task-3")
        clock_time = 161.0  # all expired
        # Trigger purge via check
        dedup.check("x", "y", "task-new")
        assert len(dedup._records) == 0
