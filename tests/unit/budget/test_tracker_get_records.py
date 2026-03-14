"""Tests for CostTracker.get_records() method."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.budget.tracker import CostTracker
from tests.unit.budget.conftest import make_cost_record


@pytest.mark.unit
class TestGetRecords:
    """Tests for the get_records query method."""

    async def test_empty_tracker_returns_empty_tuple(self) -> None:
        tracker = CostTracker()
        result = await tracker.get_records()
        assert result == ()

    async def test_returns_all_records_unfiltered(self) -> None:
        tracker = CostTracker()
        r1 = make_cost_record(agent_id="alice", task_id="t1")
        r2 = make_cost_record(agent_id="bob", task_id="t2")
        await tracker.record(r1)
        await tracker.record(r2)

        result = await tracker.get_records()
        assert len(result) == 2

    async def test_filter_by_agent_id(self) -> None:
        tracker = CostTracker()
        await tracker.record(make_cost_record(agent_id="alice"))
        await tracker.record(make_cost_record(agent_id="bob"))

        result = await tracker.get_records(agent_id="alice")
        assert len(result) == 1
        assert result[0].agent_id == "alice"

    async def test_filter_by_task_id(self) -> None:
        tracker = CostTracker()
        await tracker.record(make_cost_record(task_id="task-a"))
        await tracker.record(make_cost_record(task_id="task-b"))

        result = await tracker.get_records(task_id="task-a")
        assert len(result) == 1
        assert result[0].task_id == "task-a"

    async def test_filter_by_time_range(self) -> None:
        tracker = CostTracker()
        t1 = datetime(2026, 2, 10, tzinfo=UTC)
        t2 = datetime(2026, 2, 20, tzinfo=UTC)
        t3 = datetime(2026, 2, 25, tzinfo=UTC)
        await tracker.record(make_cost_record(timestamp=t1))
        await tracker.record(make_cost_record(timestamp=t2))
        await tracker.record(make_cost_record(timestamp=t3))

        result = await tracker.get_records(
            start=datetime(2026, 2, 15, tzinfo=UTC),
            end=datetime(2026, 2, 22, tzinfo=UTC),
        )
        assert len(result) == 1
        assert result[0].timestamp == t2

    async def test_combined_filters(self) -> None:
        tracker = CostTracker()
        t1 = datetime(2026, 2, 10, tzinfo=UTC)
        t2 = datetime(2026, 2, 20, tzinfo=UTC)
        await tracker.record(
            make_cost_record(agent_id="alice", task_id="t1", timestamp=t1),
        )
        await tracker.record(
            make_cost_record(agent_id="alice", task_id="t2", timestamp=t2),
        )
        await tracker.record(
            make_cost_record(agent_id="bob", task_id="t1", timestamp=t2),
        )

        result = await tracker.get_records(
            agent_id="alice",
            start=datetime(2026, 2, 15, tzinfo=UTC),
        )
        assert len(result) == 1
        assert result[0].task_id == "t2"

    async def test_returns_immutable_tuple(self) -> None:
        tracker = CostTracker()
        await tracker.record(make_cost_record())
        result = await tracker.get_records()
        assert isinstance(result, tuple)

    async def test_no_matches_returns_empty(self) -> None:
        tracker = CostTracker()
        await tracker.record(make_cost_record(agent_id="alice"))
        result = await tracker.get_records(agent_id="nonexistent")
        assert result == ()

    async def test_invalid_time_range_raises(self) -> None:
        tracker = CostTracker()
        with pytest.raises(ValueError, match=r"start.*before.*end"):
            await tracker.get_records(
                start=datetime(2026, 3, 1, tzinfo=UTC),
                end=datetime(2026, 2, 1, tzinfo=UTC),
            )

    async def test_start_inclusive_end_exclusive(self) -> None:
        tracker = CostTracker()
        boundary = datetime(2026, 2, 15, tzinfo=UTC)
        before = boundary - timedelta(seconds=1)
        after = boundary + timedelta(seconds=1)

        await tracker.record(make_cost_record(timestamp=before))
        await tracker.record(make_cost_record(timestamp=boundary))
        await tracker.record(make_cost_record(timestamp=after))

        # start=boundary should include boundary
        result = await tracker.get_records(start=boundary)
        assert len(result) == 2

        # end=boundary should exclude boundary
        result = await tracker.get_records(end=boundary)
        assert len(result) == 1
        assert result[0].timestamp == before
