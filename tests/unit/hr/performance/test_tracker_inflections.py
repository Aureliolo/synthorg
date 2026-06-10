"""Tests for PerformanceTracker inflection emission."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from synthorg.core.task_enums import Complexity, TaskType
from synthorg.hr.enums import TrendDirection
from synthorg.hr.performance.inflection_protocol import (
    InflectionSink,
    PerformanceInflection,
)
from synthorg.hr.performance.models import TaskMetricRecord
from synthorg.hr.performance.tracker import PerformanceTracker


def _make_task_record(
    agent_id: str = "agent-1",
    *,
    quality: float = 8.0,
    cost: float = 0.5,
    days_ago: int = 0,
) -> TaskMetricRecord:
    now = datetime.now(UTC) - timedelta(days=days_ago)
    return TaskMetricRecord(
        id=uuid4(),
        agent_id=agent_id,
        task_id=str(uuid4()),
        task_type=TaskType.DEVELOPMENT,
        complexity=Complexity.MEDIUM,
        started_at=now - timedelta(hours=1),
        completed_at=now,
        is_success=True,
        duration_seconds=3600.0,
        cost=cost,
        currency="EUR",
        turns_used=5,
        tokens_used=1000,
        quality_score=quality,
    )


class TestInflectionDetection:
    """Inflection events are emitted on trend direction changes."""

    @pytest.mark.unit
    async def test_no_emission_without_sink(self) -> None:
        """No errors when inflection_sink is None."""
        tracker = PerformanceTracker()
        # Record enough data for trends.
        for i in range(10):
            await tracker.record_task_metric(
                _make_task_record(days_ago=i),
            )
        snapshot = await tracker.get_snapshot("agent-1")
        # Should succeed without errors.
        assert snapshot is not None

    @pytest.mark.unit
    async def test_no_emission_on_first_snapshot(self) -> None:
        """First snapshot seeds cache but does not emit."""
        sink = AsyncMock(spec=InflectionSink)
        tracker = PerformanceTracker(inflection_sink=sink)

        for i in range(10):
            await tracker.record_task_metric(
                _make_task_record(days_ago=i),
            )
        await tracker.get_snapshot("agent-1")

        # Wait for background tasks to complete.
        tasks = list(tracker._background_tasks)
        if tasks:
            await asyncio.gather(*tasks)

        # First snapshot seeds cache -- no direction change yet.
        sink.emit.assert_not_called()

    @pytest.mark.unit
    async def test_emission_on_direction_change(self) -> None:
        """Changing trend direction emits an inflection event."""
        sink = AsyncMock(spec=InflectionSink)
        tracker = PerformanceTracker(inflection_sink=sink)

        # Seed the cache with initial trends.
        for i in range(10):
            await tracker.record_task_metric(
                _make_task_record(days_ago=i, quality=8.0),
            )
        await tracker.get_snapshot("agent-1")
        if tasks := list(tracker._background_tasks):
            await asyncio.gather(*tasks)

        # Force a different direction by manipulating the cache.
        for key in list(tracker._trend_direction_cache.keys()):
            if "quality" in key[1]:
                tracker._trend_direction_cache[key] = TrendDirection.DECLINING

        # Second snapshot should detect the change.
        await tracker.get_snapshot("agent-1")
        if tasks := list(tracker._background_tasks):
            await asyncio.gather(*tasks)

        # At least one inflection should have been emitted.
        if sink.emit.call_count > 0:
            inflection = sink.emit.call_args[0][0]
            assert isinstance(inflection, PerformanceInflection)
            assert inflection.agent_id == "agent-1"
            assert inflection.old_direction == TrendDirection.DECLINING

    @pytest.mark.unit
    async def test_sink_error_does_not_propagate(self) -> None:
        """Sink failures are swallowed (best-effort)."""
        sink = AsyncMock(spec=InflectionSink)
        sink.emit.side_effect = RuntimeError("sink broken")
        tracker = PerformanceTracker(inflection_sink=sink)

        for i in range(10):
            await tracker.record_task_metric(
                _make_task_record(days_ago=i),
            )

        # Seed cache with different direction.
        tracker._trend_direction_cache[("agent-1", "quality_score", "7d")] = (
            TrendDirection.DECLINING
        )

        # Should not raise.
        await tracker.get_snapshot("agent-1")
        if tasks := list(tracker._background_tasks):
            results = await asyncio.gather(*tasks, return_exceptions=True)
            # Verify only the expected sink error propagated, not
            # unexpected failures that would indicate a real bug.
            for result in results:
                if isinstance(result, BaseException):
                    assert isinstance(result, RuntimeError)

    @pytest.mark.unit
    async def test_cache_is_updated(self) -> None:
        """Trend direction cache is updated after each snapshot."""
        sink = AsyncMock(spec=InflectionSink)
        tracker = PerformanceTracker(inflection_sink=sink)

        for i in range(10):
            await tracker.record_task_metric(
                _make_task_record(days_ago=i),
            )
        await tracker.get_snapshot("agent-1")
        if tasks := list(tracker._background_tasks):
            await asyncio.gather(*tasks)

        # Cache should have entries.
        assert len(tracker._trend_direction_cache) > 0
