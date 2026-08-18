"""Unit tests for the cockpit live-activity service."""

from datetime import UTC, datetime, timedelta
from typing import override
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
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrame,
    FlightRecorderFrameAggregate,
    FlightRecorderFrameFilterSpec,
)
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
    execution_id: str | None = None,
) -> FlightRecorderFrame:
    return FlightRecorderFrame(
        id=NotBlankStr(f"{task_id}-{turn}"),
        execution_id=NotBlankStr(execution_id or f"exec-{task_id}"),
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
    started_at: datetime | None = None,
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
        started_at=started_at or last_active,
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


class _FramesLandingBetweenReads(FakeFlightRecorderFrameRepository):
    """A store whose batch lands between the service's two aggregate reads.

    The task-wide total and the execution-scoped deduction are separate
    round-trips, so frames written in the gap are seen by the second read
    and not the first.
    """

    def __init__(self, late: tuple[FlightRecorderFrame, ...]) -> None:
        super().__init__()
        self._late = late
        self._reads = 0

    @override
    async def get_aggregate(
        self,
        filter_spec: FlightRecorderFrameFilterSpec,
    ) -> FlightRecorderFrameAggregate:
        """Answer this read, then land the batch behind the first one.

        Returns:
            The aggregate as of this read.
        """
        self._reads += 1
        aggregate = await super().get_aggregate(filter_spec)
        if self._reads == 1:
            await self.append_many(self._late)
        return aggregate


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

    async def test_the_live_execution_is_not_counted_twice(
        self, sample_task_with_criteria: Task
    ) -> None:
        """An attempt's frames land before its live row is cleared.

        Between those two writes the aggregate and the live row describe the
        same spend, and a crash in the gap leaves the row EXECUTING for good,
        so the doubling is durable rather than momentary. Against a per-task
        budget that reads as a runaway that is not happening.
        """
        repo = FakeFlightRecorderFrameRepository()
        # An earlier attempt, plus the live one already written to frames.
        await repo.append(
            _frame(task_id="t1", turn=2, cost=0.4, ts=_NOW - timedelta(minutes=9)),
        )
        await repo.append(
            _frame(
                task_id="t1",
                turn=3,
                cost=1.0,
                ts=_NOW - timedelta(minutes=1),
                execution_id="live-exec-t1",
            ),
        )
        task = _task(sample_task_with_criteria, task_id="t1", agent="alice", budget=1.0)
        service = _service(
            (task,),
            repo,
            _live(
                agent="alice",
                task_id="t1",
                turns=3,
                cost=1.0,
                last_active=_NOW - timedelta(minutes=1),
            ),
        )

        snapshot = await service.get_live_snapshot(
            stuck_idle_minutes=10.0,
            runaway_cost_percent=150.0,
        )
        activity = snapshot.agents[0]

        # 0.4 recorded elsewhere + 1.0 in flight, NOT 0.4 + 1.0 + 1.0.
        assert activity.cost == pytest.approx(1.4)
        assert activity.is_runaway is False

    async def test_a_batch_landing_between_the_reads_does_not_invert_the_cost(
        self, sample_task_with_criteria: Task
    ) -> None:
        """The two frame reads are not one snapshot.

        The task-wide total is read first and the execution-scoped deduction
        second, so a batch landing in the gap is counted only by what is
        subtracted. ``AgentActivity.cost`` is ``ge=0`` and the rows are built
        in a ``TaskGroup``, so an inverted pair takes the whole snapshot down
        rather than mis-reporting the one agent it concerns.
        """
        repo = _FramesLandingBetweenReads(
            (
                _frame(
                    task_id="t1",
                    turn=4,
                    cost=1.0,
                    ts=_NOW - timedelta(seconds=30),
                    execution_id="live-exec-t1",
                ),
            ),
        )
        task = _task(sample_task_with_criteria, task_id="t1", agent="alice", budget=0.0)
        service = _service(
            (task,),
            repo,
            _live(
                agent="alice",
                task_id="t1",
                turns=4,
                cost=0.2,
                last_active=_NOW - timedelta(seconds=30),
            ),
        )

        snapshot = await service.get_live_snapshot(
            stuck_idle_minutes=10.0,
            runaway_cost_percent=150.0,
        )

        # Total read as 0.0 against a 1.0 deduction: floored, then the live
        # figure, rather than -0.8 and a snapshot that never returns.
        assert len(snapshot.agents) == 1
        assert snapshot.agents[0].cost == pytest.approx(0.2)

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

    async def test_a_task_that_waited_in_the_queue_is_not_stuck_once_running(
        self, sample_task_with_criteria: Task
    ) -> None:
        """Filing time measures time in the QUEUE, not time without progress.

        A task filed long before a dispatcher reached it would read stuck the
        instant it started, while its agent was actively mid-turn, and log a
        WARNING on every poll. The row written at dispatch is what prevents
        it: a running task carries a live timestamp from pickup, so filing
        time is consulted only for a task no run has claimed.
        """
        repo = FakeFlightRecorderFrameRepository()
        task = _task(
            sample_task_with_criteria, task_id="t1", agent="alice", budget=0.0
        ).model_copy(update={"created_at": _NOW - timedelta(minutes=45)})
        service = _service(
            (task,),
            repo,
            _live(
                agent="alice",
                task_id="t1",
                turns=0,
                cost=0.0,
                last_active=_NOW - timedelta(seconds=5),
            ),
        )

        snapshot = await service.get_live_snapshot(
            stuck_idle_minutes=10.0,
            runaway_cost_percent=150.0,
        )
        assert snapshot.agents[0].is_stuck is False


class TestRunawaySpendIsSeenWhileItIsHappening:
    """The marker exists to catch a run burning budget, not to report it after.

    Reading spend from finished frames alone left every in-flight row at
    zero, so the check could not fire until the run it was watching had
    already stopped.
    """

    async def test_a_live_run_over_its_budget_is_flagged(
        self, sample_task_with_criteria: Task
    ) -> None:
        repo = FakeFlightRecorderFrameRepository()
        task = _task(sample_task_with_criteria, task_id="t1", agent="alice", budget=1.0)
        service = _service(
            (task,),
            repo,
            _live(
                agent="alice",
                task_id="t1",
                turns=8,
                cost=2.0,
                last_active=_NOW - timedelta(seconds=5),
            ),
        )

        snapshot = await service.get_live_snapshot(
            stuck_idle_minutes=10.0,
            runaway_cost_percent=150.0,
        )
        assert snapshot.agents[0].is_runaway is True
        assert snapshot.runaway_agents == ("alice",)

    async def test_an_earlier_attempts_spend_still_counts(
        self, sample_task_with_criteria: Task
    ) -> None:
        """A retry starts a new execution at zero; the budget is per TASK.

        Reading the live figure alone would let a task that already burned
        most of its budget read healthy for the whole of its next attempt,
        then flip the moment that attempt ended and the frames answered
        again.
        """
        repo = FakeFlightRecorderFrameRepository()
        await repo.append(
            _frame(task_id="t1", turn=4, cost=1.4, ts=_NOW - timedelta(minutes=20)),
        )
        task = _task(sample_task_with_criteria, task_id="t1", agent="alice", budget=1.0)
        service = _service(
            (task,),
            repo,
            _live(
                agent="alice",
                task_id="t1",
                turns=1,
                cost=0.2,
                last_active=_NOW - timedelta(seconds=5),
            ),
        )

        snapshot = await service.get_live_snapshot(
            stuck_idle_minutes=10.0,
            runaway_cost_percent=150.0,
        )
        assert snapshot.agents[0].cost == pytest.approx(1.6)
        assert snapshot.agents[0].is_runaway is True
