# mypy: disable-error-code="explicit-any"
"""Concurrency regression tests for PerformanceTracker.

The tracker's ``_metrics_lock`` historically only protected
``record_task_metric`` and the inflection cache.
``record_coordination_contributions`` (synchronous) and
``record_collaboration_event`` (async) both mutated shared dicts without
the lock, which is an open defect waiting for an ``await`` to be inserted
into either body. These tests pin the locked invariant.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from typeguard import suppress_type_checks

from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.attribution import AgentContribution
from synthorg.hr.performance.tracker import PerformanceTracker

from .conftest import make_collab_metric, make_task_metric

_AGENT_ID = "agent-conc-001"


def _make_contribution(
    *,
    agent_id: str = _AGENT_ID,
    subtask_id: str = "subtask-001",
    score: float = 1.0,
) -> AgentContribution:
    return AgentContribution(
        agent_id=NotBlankStr(agent_id),
        subtask_id=NotBlankStr(subtask_id),
        contribution_score=score,
    )


@pytest.mark.unit
class TestTrackerConcurrency:
    """Concurrent writes must not lose records."""

    async def test_concurrent_record_coordination_contributions_no_lost_writes(
        self,
    ) -> None:
        tracker = PerformanceTracker()
        n_calls = 50
        contribs_per_call = 2

        async def _record(i: int) -> None:
            batch = tuple(
                _make_contribution(subtask_id=f"st-{i}-{j}")
                for j in range(contribs_per_call)
            )
            await tracker.record_coordination_contributions(batch)

        async with asyncio.TaskGroup() as tg:
            for i in range(n_calls):
                _ = tg.create_task(_record(i))

        assert len(tracker._contributions[_AGENT_ID]) == n_calls * contribs_per_call

    async def test_concurrent_record_collaboration_event_no_lost_writes(
        self,
    ) -> None:
        tracker = PerformanceTracker()
        n_events = 50

        async def _record(i: int) -> None:
            record = make_collab_metric(
                agent_id=_AGENT_ID,
                handoff_completeness=float(i % 10) / 10.0,
            )
            await tracker.record_collaboration_event(record)

        async with asyncio.TaskGroup() as tg:
            for i in range(n_events):
                _ = tg.create_task(_record(i))

        assert len(tracker._collab_metrics[_AGENT_ID]) == n_events

    async def test_mixed_workload_record_task_and_coordination_race_free(
        self,
    ) -> None:
        tracker = PerformanceTracker()

        async def _record_task(i: int) -> None:
            await tracker.record_task_metric(
                make_task_metric(
                    agent_id=_AGENT_ID,
                    task_id=f"t-{i}",
                ),
            )

        async def _record_contrib(i: int) -> None:
            await tracker.record_coordination_contributions(
                (_make_contribution(subtask_id=f"sub-{i}"),),
            )

        async with asyncio.TaskGroup() as tg:
            for i in range(30):
                _ = tg.create_task(_record_task(i))
                _ = tg.create_task(_record_contrib(i))

        assert len(tracker._task_metrics[_AGENT_ID]) == 30
        assert len(tracker._contributions[_AGENT_ID]) == 30


@pytest.mark.unit
class TestGetSnapshotsFaultIsolation:
    """get_snapshots: failures for one agent must not poison the batch."""

    async def test_partial_failure_returns_none_for_failing_agent(self) -> None:
        tracker = PerformanceTracker()
        snapshot_a = object()
        snapshot_c = object()

        async def fake_get_snapshot(agent_id: NotBlankStr, **_: object) -> object:
            if str(agent_id) == "bad":
                msg = "snapshot unavailable"
                raise RuntimeError(msg)
            if str(agent_id) == "good-a":
                return snapshot_a
            return snapshot_c

        tracker.get_snapshot = AsyncMock(side_effect=fake_get_snapshot)  # type: ignore[method-assign]

        with suppress_type_checks():
            results = await tracker.get_snapshots(
                (
                    NotBlankStr("good-a"),
                    NotBlankStr("bad"),
                    NotBlankStr("good-c"),
                ),
            )

        assert len(results) == 3
        assert results[0] is snapshot_a
        assert results[1] is None
        assert results[2] is snapshot_c

    async def test_empty_input_returns_empty_tuple(self) -> None:
        tracker = PerformanceTracker()
        assert await tracker.get_snapshots(()) == ()

    async def test_rejects_oversized_batch(self) -> None:
        """Exceeding ``MAX_BATCH_SNAPSHOTS_LOOKUP`` must raise ``ValueError``."""
        from synthorg.hr.performance.tracker import (
            MAX_BATCH_SNAPSHOTS_LOOKUP,
        )

        tracker = PerformanceTracker()
        agent_ids = tuple(
            NotBlankStr(f"agent-{i}") for i in range(MAX_BATCH_SNAPSHOTS_LOOKUP + 1)
        )
        with pytest.raises(ValueError, match="exceeds"):
            await tracker.get_snapshots(agent_ids)
