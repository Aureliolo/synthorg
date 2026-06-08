"""Acceptance: watch a run, spot a stuck agent, intervene, replay it.

Exercises the mission-control cockpit end-to-end through the real
services it wires (live-activity aggregation, steering service,
flight-recorder replay) over recorded per-turn frames. This is the
operator flow #1981 requires: during a run the operator can see
progress, identify a stuck agent and intervene (steer the project),
and afterwards replay the run step-by-step with content.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import InterventionKind
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.cockpit import CockpitService
from synthorg.engine.flight_recording import (
    FlightRecorderService,
    PersistenceFlightRecorderSink,
)
from synthorg.engine.intervention import NoOpSupersessionProposer, SteeringService
from synthorg.engine.task_engine import TaskEngine
from synthorg.persistence.flight_recorder_protocol import FlightRecorderFrame
from tests._shared import FakeClock, as_uuid, mock_of, sid
from tests._shared.steering import FakeBrainService
from tests.unit.api.fakes import (
    FakeFlightRecorderFrameRepository,
    FakeProjectBrainRepository,
)

pytestmark = pytest.mark.e2e

_NOW = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
_EXEC = "exec-stuck-run"
_TASK = "task-stuck"


def _frame(turn: int, *, response: str, ts: datetime) -> FlightRecorderFrame:
    return FlightRecorderFrame(
        id=NotBlankStr(f"{_EXEC}-{turn}"),
        execution_id=NotBlankStr(_EXEC),
        task_id=NotBlankStr(sid(_TASK)),
        agent_id=NotBlankStr("agent-1"),
        turn_index=turn,
        timestamp=ts,
        response_summary=response,
        decision="completed",
        cost=0.01,
        status=TaskStatus.IN_PROGRESS,
    )


async def test_detect_stuck_intervene_then_replay() -> None:
    repo = FakeFlightRecorderFrameRepository()
    clock = FakeClock(start=_NOW)

    # 1. A run records frames as it works, then stalls: the latest frame
    #    is older than the stuck threshold.
    stale = _NOW - timedelta(minutes=30)
    await PersistenceFlightRecorderSink(repo).record_frames(
        (
            _frame(1, response="searching the codebase", ts=stale),
            _frame(2, response="still investigating", ts=stale),
        ),
    )

    # 2. The cockpit live snapshot flags the agent as stuck.
    # ``Task`` is a frozen Pydantic model, not a Protocol, so we
    # construct it directly. ``mock_of[Task]`` would fail because
    # Pydantic v2 field descriptors are not exposed as ``dir()``
    # attributes that ``create_autospec`` can introspect.
    stuck_task = Task(
        id=as_uuid(_TASK),
        title=NotBlankStr("Stuck investigation"),
        description=NotBlankStr("Reproduce the runaway agent scenario."),
        type=TaskType.DEVELOPMENT,
        project=NotBlankStr("mission-control-e2e"),
        created_by=NotBlankStr("operator"),
        assigned_to=NotBlankStr("agent-1"),
        status=TaskStatus.IN_PROGRESS,
        budget_limit=0.0,
    )
    task_engine = mock_of[TaskEngine](
        list_tasks=AsyncMock(side_effect=[((stuck_task,), 1), ((), 0)]),
    )
    cockpit = CockpitService(task_engine, repo, clock=clock)
    snapshot = await cockpit.get_live_snapshot(
        stuck_idle_minutes=10.0,
        runaway_cost_percent=150.0,
    )
    assert snapshot.stuck_agents == ("agent-1",)
    activity = snapshot.agents[0]
    assert activity.execution_id == _EXEC

    # 3. The operator steers the project with a hint; it is recorded in
    #    the project brain and surfaces as the active directive in-flight
    #    agents adopt at their next safe boundary.
    brain_repo = FakeProjectBrainRepository()
    steering = SteeringService(
        brain_service=FakeBrainService(brain_repo, clock=clock),  # type: ignore[arg-type]
        brain_repo=brain_repo,
        task_engine=task_engine,
        proposer=NoOpSupersessionProposer(),
        clock=clock,
    )
    result = await steering.issue(
        project_id=NotBlankStr("mission-control-e2e"),
        kind=InterventionKind.HINT,
        text=NotBlankStr("you seem stuck; try a narrower search"),
        author=NotBlankStr("operator"),
    )
    active = await steering.list_active(project_id=NotBlankStr("mission-control-e2e"))
    assert len(active) == 1
    assert active[0].entry_id == result.directive_id
    assert active[0].kind is InterventionKind.HINT
    assert active[0].text == "you seem stuck; try a narrower search"

    # 4. Afterwards the run replays step-by-step with content.
    recorder = FlightRecorderService(repo)
    timeline = await recorder.get_frames(_EXEC)
    assert [f.turn_index for f in timeline] == [2, 1]
    view = await recorder.seek(_EXEC, 2)
    assert [f.turn_index for f in view.frames] == [1, 2]
    assert view.current_frame is not None
    assert view.current_frame.response_summary == "still investigating"
    assert view.cumulative_cost == pytest.approx(0.02)
