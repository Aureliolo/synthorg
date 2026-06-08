"""Tests for coordination metrics store."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.budget.coordination_metric_models import CoordinationMetrics
from synthorg.budget.coordination_store import (
    CoordinationMetricsRecord,
    CoordinationMetricsStore,
)


def _make_record(
    *,
    task_id: str = "task-1",
    agent_id: str | None = "agent-a",
    timestamp: datetime | None = None,
    team_size: int = 3,
) -> CoordinationMetricsRecord:
    return CoordinationMetricsRecord(
        task_id=task_id,
        agent_id=agent_id,
        computed_at=timestamp or datetime(2026, 4, 1, tzinfo=UTC),
        team_size=team_size,
        metrics=CoordinationMetrics(),
    )


@pytest.mark.unit
class TestCoordinationMetricsStore:
    def test_empty_store(self) -> None:
        store = CoordinationMetricsStore()
        assert store.count() == 0
        results, total = store.query()
        assert results == ()
        assert total == 0

    def test_record_and_query(self) -> None:
        store = CoordinationMetricsStore()
        rec = _make_record()
        store.record(rec)
        assert store.count() == 1
        results, total = store.query()
        assert len(results) == 1
        assert total == 1
        assert results[0].task_id == "task-1"

    def test_query_newest_first(self) -> None:
        store = CoordinationMetricsStore()
        t1 = datetime(2026, 4, 1, tzinfo=UTC)
        t2 = t1 + timedelta(hours=1)
        store.record(_make_record(timestamp=t1, task_id="old"))
        store.record(_make_record(timestamp=t2, task_id="new"))
        results, _ = store.query()
        assert results[0].task_id == "new"
        assert results[1].task_id == "old"

    def test_filter_by_task_id(self) -> None:
        store = CoordinationMetricsStore()
        store.record(_make_record(task_id="a"))
        store.record(_make_record(task_id="b"))
        results, total = store.query(task_id="a")
        assert len(results) == 1
        assert total == 1
        assert results[0].task_id == "a"

    def test_filter_by_agent_id(self) -> None:
        store = CoordinationMetricsStore()
        store.record(_make_record(agent_id="alice"))
        store.record(_make_record(agent_id="bob"))
        results, total = store.query(agent_id="alice")
        assert len(results) == 1
        assert total == 1

    def test_filter_by_time_range(self) -> None:
        store = CoordinationMetricsStore()
        t1 = datetime(2026, 4, 1, tzinfo=UTC)
        t2 = t1 + timedelta(hours=1)
        t3 = t1 + timedelta(hours=2)
        store.record(_make_record(timestamp=t1, task_id="a"))
        store.record(_make_record(timestamp=t2, task_id="b"))
        store.record(_make_record(timestamp=t3, task_id="c"))
        results, total = store.query(since=t1, until=t2)
        assert len(results) == 2
        assert total == 2

    def test_eviction(self) -> None:
        store = CoordinationMetricsStore(max_entries=3)
        for i in range(5):
            store.record(_make_record(task_id=f"task-{i}"))
        assert store.count() == 3
        results, _ = store.query()
        task_ids = [r.task_id for r in results]
        assert "task-0" not in task_ids
        assert "task-4" in task_ids

    def test_total_matches_exceeds_limit(self) -> None:
        store = CoordinationMetricsStore()
        for i in range(5):
            store.record(_make_record(task_id=f"task-{i}"))
        results, total = store.query(limit=2)
        assert len(results) == 2
        assert total == 5

    def test_max_entries_validation(self) -> None:
        with pytest.raises(ValueError, match="max_entries must be >= 1"):
            CoordinationMetricsStore(max_entries=0)
