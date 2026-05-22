"""Unit tests for the cockpit live-activity service."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from tests._shared import FakeClock, mock_of
from tests.unit.api.fakes import FakeFlightRecorderFrameRepository

from synthorg.core.enums import TaskStatus
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.cockpit import CockpitService
from synthorg.engine.task_engine import TaskEngine
from synthorg.persistence.flight_recorder_protocol import FlightRecorderFrame

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)


def _task(base: Task, *, task_id: str, agent: str, budget: float) -> Task:
    return base.model_copy(
        update={
            "id": NotBlankStr(task_id),
            "status": TaskStatus.IN_PROGRESS,
            "assigned_to": NotBlankStr(agent),
            "budget_limit": budget,
        },
    )


def _frame(
    *,
    task_id: str,
    turn: int,
    cost: float,
    ts: datetime,
) -> FlightRecorderFrame:
    return FlightRecorderFrame(
        id=NotBlankStr(f"{task_id}-{turn}"),
        execution_id=NotBlankStr(f"exec-{task_id}"),
        task_id=NotBlankStr(task_id),
        agent_id=NotBlankStr("agent"),
        turn_index=turn,
        timestamp=ts,
        cost=cost,
        status=TaskStatus.IN_PROGRESS,
    )


def _service(
    tasks: tuple[Task, ...],
    repo: FakeFlightRecorderFrameRepository,
) -> CockpitService:
    task_engine = mock_of[TaskEngine](
        list_tasks=AsyncMock(side_effect=[(tasks, len(tasks)), ((), 0)]),
    )
    return CockpitService(
        task_engine,
        repo,
        clock=FakeClock(start=_NOW),
    )


class TestCockpitService:
    async def test_snapshot_lists_active_work(
        self, sample_task_with_criteria: Task
    ) -> None:
        repo = FakeFlightRecorderFrameRepository()
        await repo.append(
            _frame(task_id="t1", turn=2, cost=0.4, ts=_NOW - timedelta(minutes=1)),
        )
        task = _task(sample_task_with_criteria, task_id="t1", agent="alice", budget=0.0)
        service = _service((task,), repo)

        snapshot = await service.get_live_snapshot(
            stuck_idle_minutes=10.0,
            runaway_cost_percent=150.0,
        )
        assert snapshot.active_count == 1
        activity = snapshot.agents[0]
        assert activity.agent_id == "alice"
        assert activity.turn_count == 2
        assert activity.cost == pytest.approx(0.4)
        assert activity.is_stuck is False
        assert snapshot.stuck_agents == ()

    async def test_idle_task_flagged_stuck(
        self, sample_task_with_criteria: Task
    ) -> None:
        repo = FakeFlightRecorderFrameRepository()
        await repo.append(
            _frame(task_id="t1", turn=1, cost=0.1, ts=_NOW - timedelta(minutes=30)),
        )
        task = _task(sample_task_with_criteria, task_id="t1", agent="bob", budget=0.0)
        service = _service((task,), repo)

        snapshot = await service.get_live_snapshot(
            stuck_idle_minutes=10.0,
            runaway_cost_percent=150.0,
        )
        assert snapshot.agents[0].is_stuck is True
        assert snapshot.stuck_agents == ("bob",)

    async def test_overspend_flagged_runaway(
        self, sample_task_with_criteria: Task
    ) -> None:
        repo = FakeFlightRecorderFrameRepository()
        # budget 1.0, runaway at 150% => cost > 1.5 triggers.
        await repo.append(
            _frame(task_id="t1", turn=1, cost=2.0, ts=_NOW - timedelta(minutes=1)),
        )
        task = _task(sample_task_with_criteria, task_id="t1", agent="carol", budget=1.0)
        service = _service((task,), repo)

        snapshot = await service.get_live_snapshot(
            stuck_idle_minutes=10.0,
            runaway_cost_percent=150.0,
        )
        assert snapshot.agents[0].is_runaway is True
        assert snapshot.runaway_agents == ("carol",)

    async def test_no_active_work_empty_snapshot(self) -> None:
        repo = FakeFlightRecorderFrameRepository()
        service = _service((), repo)

        snapshot = await service.get_live_snapshot(
            stuck_idle_minutes=10.0,
            runaway_cost_percent=150.0,
        )
        assert snapshot.active_count == 0
        assert snapshot.agents == ()
        assert snapshot.total_cost == pytest.approx(0.0)
