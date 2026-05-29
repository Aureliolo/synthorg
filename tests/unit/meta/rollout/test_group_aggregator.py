# mypy: disable-error-code="explicit-any"
"""Tests for the GroupSignalAggregator protocol and default impl."""

from datetime import UTC, datetime
from typing import Any

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import (
    AgentPerformanceSnapshot,
    WindowMetrics,
)
from synthorg.meta.rollout.group_aggregator import (
    GroupSamples,
    GroupSignalAggregator,
    TrackerGroupAggregator,
)

pytestmark = pytest.mark.unit


class _FakeTracker:
    def __init__(
        self,
        snapshots: dict[str, AgentPerformanceSnapshot],
    ) -> None:
        self._snapshots = snapshots
        self.calls: list[tuple[str, datetime]] = []

    async def get_snapshot(
        self,
        agent_id: str,
        *,
        now: datetime,
    ) -> AgentPerformanceSnapshot:
        self.calls.append((agent_id, now))
        return self._snapshots[agent_id]


def _snapshot(
    *,
    agent_id: str,
    quality: float | None,
    success: float | None,
    cost_per_task: float | None,
    tasks_completed: int = 5,
) -> AgentPerformanceSnapshot:
    window = WindowMetrics(
        window_size=NotBlankStr("7d"),
        data_point_count=tasks_completed + 0,
        tasks_completed=tasks_completed,
        tasks_failed=0,
        avg_quality_score=quality,
        avg_cost_per_task=cost_per_task,
        success_rate=success,
        currency="EUR",
    )
    return AgentPerformanceSnapshot(
        agent_id=NotBlankStr(agent_id),
        computed_at=datetime(2026, 4, 17, tzinfo=UTC),
        windows=(window,),
        trends=(),
        overall_quality_score=quality,
        overall_collaboration_score=None,
    )


class TestGroupSamplesModel:
    def test_empty_samples_valid(self) -> None:
        samples = GroupSamples(
            agent_ids=(),
            quality_samples=(),
            success_samples=(),
            spend_samples=(),
        )
        assert samples.quality_samples == ()

    def test_rejects_non_finite(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="finite"):
            GroupSamples(
                agent_ids=(NotBlankStr("a"),),
                quality_samples=(float("nan"),),
                success_samples=(0.5,),
                spend_samples=(1.0,),
            )

    def test_freezes(self) -> None:
        from pydantic import ValidationError

        samples = GroupSamples(
            agent_ids=(NotBlankStr("a"),),
            quality_samples=(7.0,),
            success_samples=(0.8,),
            spend_samples=(1.5,),
        )
        with pytest.raises(ValidationError, match="frozen"):
            samples.quality_samples = ()  # type: ignore[misc]


class TestTrackerGroupAggregator:
    async def test_empty_agent_list_returns_empty_samples(self) -> None:
        tracker = _FakeTracker({})
        agg: Any = TrackerGroupAggregator(tracker=tracker)  # type: ignore[arg-type]
        until = datetime(2026, 4, 17, tzinfo=UTC)
        since = datetime(2026, 4, 10, tzinfo=UTC)
        samples = await agg.aggregate_for_agents(
            agent_ids=(),
            since=since,
            until=until,
        )
        assert samples.quality_samples == ()
        assert samples.success_samples == ()
        assert samples.spend_samples == ()
        assert tracker.calls == []

    async def test_collects_per_agent_samples(self) -> None:
        snapshots = {
            "a1": _snapshot(
                agent_id="a1",
                quality=7.5,
                success=0.9,
                cost_per_task=1.0,
                tasks_completed=10,
            ),
            "a2": _snapshot(
                agent_id="a2",
                quality=6.0,
                success=0.8,
                cost_per_task=2.0,
                tasks_completed=5,
            ),
        }
        tracker = _FakeTracker(snapshots)
        agg: Any = TrackerGroupAggregator(tracker=tracker)  # type: ignore[arg-type]
        until = datetime(2026, 4, 17, tzinfo=UTC)
        since = datetime(2026, 4, 10, tzinfo=UTC)
        samples = await agg.aggregate_for_agents(
            agent_ids=(NotBlankStr("a1"), NotBlankStr("a2")),
            since=since,
            until=until,
        )
        assert samples.agent_ids == (NotBlankStr("a1"), NotBlankStr("a2"))
        assert samples.quality_samples == (7.5, 6.0)
        assert samples.success_samples == (0.9, 0.8)
        # spend = avg_cost_per_task * tasks_completed
        assert samples.spend_samples == (10.0, 10.0)

    async def test_skips_agents_with_missing_cost_per_task(self) -> None:
        snapshots = {
            "a1": _snapshot(
                agent_id="a1",
                quality=7.0,
                success=0.9,
                cost_per_task=None,
            ),
            "a2": _snapshot(
                agent_id="a2",
                quality=8.0,
                success=0.85,
                cost_per_task=1.5,
            ),
        }
        tracker = _FakeTracker(snapshots)
        agg: Any = TrackerGroupAggregator(tracker=tracker)  # type: ignore[arg-type]
        samples = await agg.aggregate_for_agents(
            agent_ids=(NotBlankStr("a1"), NotBlankStr("a2")),
            since=datetime(2026, 4, 10, tzinfo=UTC),
            until=datetime(2026, 4, 17, tzinfo=UTC),
        )
        # a1 has no cost_per_task -> excluded entirely (alignment preserved).
        assert samples.agent_ids == (NotBlankStr("a2"),)
        assert samples.quality_samples == (8.0,)

    async def test_skips_agents_with_missing_success_rate(self) -> None:
        snapshots = {
            "a1": _snapshot(
                agent_id="a1",
                quality=7.0,
                success=None,
                cost_per_task=1.0,
            ),
            "a2": _snapshot(
                agent_id="a2",
                quality=8.0,
                success=0.85,
                cost_per_task=1.5,
            ),
        }
        tracker = _FakeTracker(snapshots)
        agg: Any = TrackerGroupAggregator(tracker=tracker)  # type: ignore[arg-type]
        samples = await agg.aggregate_for_agents(
            agent_ids=(NotBlankStr("a1"), NotBlankStr("a2")),
            since=datetime(2026, 4, 10, tzinfo=UTC),
            until=datetime(2026, 4, 17, tzinfo=UTC),
        )
        assert samples.agent_ids == (NotBlankStr("a2"),)
        assert samples.success_samples == (0.85,)

    async def test_skips_agents_with_missing_quality(self) -> None:
        snapshots = {
            "a1": _snapshot(
                agent_id="a1",
                quality=None,
                success=0.9,
                cost_per_task=1.0,
            ),
            "a2": _snapshot(
                agent_id="a2",
                quality=8.0,
                success=0.85,
                cost_per_task=1.5,
            ),
        }
        tracker = _FakeTracker(snapshots)
        agg: Any = TrackerGroupAggregator(tracker=tracker)  # type: ignore[arg-type]
        samples = await agg.aggregate_for_agents(
            agent_ids=(NotBlankStr("a1"), NotBlankStr("a2")),
            since=datetime(2026, 4, 10, tzinfo=UTC),
            until=datetime(2026, 4, 17, tzinfo=UTC),
        )
        # a1 has no quality -> excluded entirely from samples to keep
        # the three sample tuples aligned by agent.
        assert samples.agent_ids == (NotBlankStr("a2"),)
        assert samples.quality_samples == (8.0,)
        assert samples.success_samples == (0.85,)

    async def test_satisfies_protocol(self) -> None:
        tracker = _FakeTracker({})
        agg = TrackerGroupAggregator(tracker=tracker)  # type: ignore[arg-type]
        assert isinstance(agg, GroupSignalAggregator)
