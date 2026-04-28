"""Unit tests for :class:`TaskTimingTracker`.

The tracker is a sibling of :class:`VersionTracker`: in-memory
single-writer per-task creation timestamps used by the task-engine
apply path to compute durations for ``synthorg_task_runs_total`` /
``synthorg_task_duration_seconds``. Exercise overwrite semantics,
absence-as-None, removal idempotence, and the UTC validator.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from synthorg.engine.task_engine_version import TaskTimingTracker

pytestmark = pytest.mark.unit


def test_record_and_get_round_trips_utc_datetime() -> None:
    tracker = TaskTimingTracker()
    when = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    tracker.record_creation("task-1", when)
    assert tracker.get_creation("task-1") == when


def test_get_creation_returns_none_for_unknown_task() -> None:
    tracker = TaskTimingTracker()
    assert tracker.get_creation("missing") is None


def test_record_creation_overwrites_previous_entry() -> None:
    tracker = TaskTimingTracker()
    first = datetime(2026, 1, 1, tzinfo=UTC)
    second = datetime(2026, 1, 2, tzinfo=UTC)
    tracker.record_creation("task-1", first)
    tracker.record_creation("task-1", second)
    assert tracker.get_creation("task-1") == second


def test_remove_drops_entry() -> None:
    tracker = TaskTimingTracker()
    tracker.record_creation("task-1", datetime.now(UTC))
    tracker.remove("task-1")
    assert tracker.get_creation("task-1") is None


def test_remove_unknown_task_is_idempotent() -> None:
    tracker = TaskTimingTracker()
    # Should not raise.
    tracker.remove("never-existed")


def test_record_creation_rejects_naive_datetime() -> None:
    tracker = TaskTimingTracker()
    naive = datetime(2026, 1, 1, 12, 0, 0)  # noqa: DTZ001 -- naive on purpose to test the validator
    with pytest.raises(ValueError, match="UTC datetime"):
        tracker.record_creation("task-1", naive)


def test_record_creation_rejects_non_utc_timezone() -> None:
    tracker = TaskTimingTracker()
    plus_two = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    with pytest.raises(ValueError, match="UTC datetime"):
        tracker.record_creation("task-1", plus_two)
