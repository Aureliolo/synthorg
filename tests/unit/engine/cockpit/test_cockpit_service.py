"""Unit tests for the cockpit live-activity service."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from synthorg.budget.currency import CurrencyCode
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_state import AgentRuntimeState, ExecutionStatus
from synthorg.engine.cockpit import CockpitService
from synthorg.engine.task_engine import TaskEngine
from synthorg.persistence.agent_state_protocol import AgentStateRepository
from synthorg.persistence.flight_recorder_protocol import FlightRecorderFrame
from tests._shared import FakeClock, mock_of
from tests.unit.api.fakes import FakeFlightRecorderFrameRepository

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


def _live(
    *,
    agent: str,
    task_id: str,
    turns: int,
    cost: float,
    last_active: datetime,
    status: ExecutionStatus = ExecutionStatus.EXECUTING,
) -> AgentRuntimeState:
    return AgentRuntimeState(
        agent_id=NotBlankStr(agent),
        execution_id=NotBlankStr(f"live-exec-{task_id}"),
        task_id=NotBlankStr(task_id),
        status=status,
        turn_count=turns,
        accumulated_cost=cost,
        currency=CurrencyCode("EUR"),
        last_activity_at=last_active,
        started_at=last_active,
    )


def _service(
    tasks: tuple[Task, ...],
    repo: FakeFlightRecorderFrameRepository,
    live: AgentRuntimeState | None = None,
) -> CockpitService:
    task_engine = mock_of[TaskEngine](
        list_tasks=AsyncMock(side_effect=[(tasks, len(tasks)), ((), 0)]),
    )
    states = mock_of[AgentStateRepository](get=AsyncMock(return_value=live))
    return CockpitService(
        task_engine,
        repo,
        agent_states=states,
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


class TestARunStillGoingIsReadFromItsLiveState:
    """Frames exist only once a run finishes, so they cannot answer for one in flight.

    A live run recorded 35 turns and real spend while this surface reported
    ``turn 0  US$ 0.00`` for every row, because the only store it read was
    built from finished runs.
    """

    async def test_live_state_answers_for_the_task_the_agent_holds(
        self, sample_task_with_criteria: Task
    ) -> None:
        repo = FakeFlightRecorderFrameRepository()
        task = _task(sample_task_with_criteria, task_id="t1", agent="alice", budget=0.0)
        service = _service(
            (task,),
            repo,
            _live(
                agent="alice",
                task_id="t1",
                turns=35,
                cost=1.25,
                last_active=_NOW - timedelta(minutes=1),
            ),
        )

        activity = (
            await service.get_live_snapshot(
                stuck_idle_minutes=10.0,
                runaway_cost_percent=150.0,
            )
        ).agents[0]
        assert activity.turn_count == 35
        assert activity.cost == pytest.approx(1.25)
        assert activity.execution_id == "live-exec-t1"
        assert activity.is_stuck is False

    async def test_a_state_about_another_task_says_nothing_about_this_one(
        self, sample_task_with_criteria: Task
    ) -> None:
        """The row is keyed by agent, so it can be about a different run."""
        repo = FakeFlightRecorderFrameRepository()
        await repo.append(
            _frame(task_id="t1", turn=2, cost=0.4, ts=_NOW - timedelta(minutes=1)),
        )
        task = _task(sample_task_with_criteria, task_id="t1", agent="alice", budget=0.0)
        service = _service(
            (task,),
            repo,
            _live(
                agent="alice",
                task_id="a-different-task",
                turns=99,
                cost=9.0,
                last_active=_NOW,
            ),
        )

        activity = (
            await service.get_live_snapshot(
                stuck_idle_minutes=10.0,
                runaway_cost_percent=150.0,
            )
        ).agents[0]
        assert activity.turn_count == 2
        assert activity.cost == pytest.approx(0.4)

    async def test_a_finished_run_is_read_from_its_frames(
        self, sample_task_with_criteria: Task
    ) -> None:
        """An IDLE agent has stopped, so the recorded frames are the answer."""
        repo = FakeFlightRecorderFrameRepository()
        await repo.append(
            _frame(task_id="t1", turn=7, cost=0.9, ts=_NOW - timedelta(minutes=2)),
        )
        task = _task(sample_task_with_criteria, task_id="t1", agent="alice", budget=0.0)
        service = _service(
            (task,),
            repo,
            AgentRuntimeState.idle(
                NotBlankStr("alice"),
                currency=CurrencyCode("EUR"),
                clock=FakeClock(start=_NOW),
            ),
        )

        activity = (
            await service.get_live_snapshot(
                stuck_idle_minutes=10.0,
                runaway_cost_percent=150.0,
            )
        ).agents[0]
        assert activity.turn_count == 7
        assert activity.cost == pytest.approx(0.9)

    async def test_a_task_nothing_has_ever_driven_reads_stuck(
        self, sample_task_with_criteria: Task
    ) -> None:
        """No activity at all is the strongest evidence of stuck, not an exemption.

        Nine ``in_progress`` rows that nothing had driven since a restart read
        healthy, because the check required a last-active timestamp none of
        them had. The task's own filing time is the fallback baseline.
        """
        repo = FakeFlightRecorderFrameRepository()
        task = _task(
            sample_task_with_criteria, task_id="t1", agent="alice", budget=0.0
        ).model_copy(update={"created_at": _NOW - timedelta(hours=4)})
        service = _service((task,), repo, None)

        snapshot = await service.get_live_snapshot(
            stuck_idle_minutes=10.0,
            runaway_cost_percent=150.0,
        )
        assert snapshot.agents[0].last_active is None
        assert snapshot.agents[0].is_stuck is True
        assert snapshot.stuck_agents == ("alice",)

    async def test_a_freshly_filed_task_with_no_activity_yet_is_not_stuck(
        self, sample_task_with_criteria: Task
    ) -> None:
        """A task filed seconds ago has not had time to be stuck."""
        repo = FakeFlightRecorderFrameRepository()
        task = _task(
            sample_task_with_criteria, task_id="t1", agent="alice", budget=0.0
        ).model_copy(update={"created_at": _NOW - timedelta(seconds=20)})
        service = _service((task,), repo, None)

        snapshot = await service.get_live_snapshot(
            stuck_idle_minutes=10.0,
            runaway_cost_percent=150.0,
        )
        assert snapshot.agents[0].is_stuck is False
        assert snapshot.stuck_agents == ()
