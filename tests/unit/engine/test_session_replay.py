"""Tests for Session.replay() -- stateless session recovery from event log."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.context import AgentContext
from synthorg.engine.session import (
    EventReader,
    ReplayResult,
    Session,
    SessionEvent,
)
from synthorg.observability.events.execution import (
    EXECUTION_CONTEXT_CREATED,
    EXECUTION_CONTEXT_TURN,
    EXECUTION_ENGINE_START,
    EXECUTION_TASK_TRANSITION,
)

# ── Stub EventReader ──────────────────────────────────────────────


class StubEventReader:
    """In-memory EventReader for testing."""

    def __init__(self, events: tuple[SessionEvent, ...] = ()) -> None:
        self._events = events

    async def read_events(
        self,
        execution_id: str,
    ) -> tuple[SessionEvent, ...]:
        return tuple(e for e in self._events if e.execution_id == execution_id)


def _ts(minute: int) -> datetime:
    """Create a UTC timestamp at the given minute offset."""
    return datetime(2026, 4, 13, 12, minute, 0, tzinfo=UTC)


def _event(
    name: str,
    execution_id: str,
    minute: int,
    **data: object,
) -> SessionEvent:
    """Create a SessionEvent with convenience defaults."""
    return SessionEvent(
        event_name=name,
        timestamp=_ts(minute),
        execution_id=execution_id,
        data=dict(data),
    )


EXEC_ID = "exec-replay-001"


def _build_scenario_events(
    scenario: str,
) -> list[SessionEvent]:
    """Build event lists for parametrized completeness tests."""
    minute = 0
    events: list[SessionEvent] = []

    if scenario == "empty":
        return events

    if scenario in ("full", "start-only"):
        events.append(
            _event(EXECUTION_ENGINE_START, EXEC_ID, minute),
        )
        minute += 1

    if scenario == "full":
        events.append(
            _event(EXECUTION_CONTEXT_CREATED, EXEC_ID, minute),
        )
        minute += 1

    if scenario in ("full", "turns-only"):
        cost = 0.01
        events.append(
            _event(
                EXECUTION_CONTEXT_TURN,
                EXEC_ID,
                minute,
                turn=1,
                cost=cost,
            ),
        )
        minute += 1
        events.append(
            _event(
                EXECUTION_CONTEXT_TURN,
                EXEC_ID,
                minute,
                turn=2,
                cost=cost,
            ),
        )
        minute += 1

    if scenario == "full":
        events.append(
            _event(
                EXECUTION_TASK_TRANSITION,
                EXEC_ID,
                minute,
                target_status=TaskStatus.IN_PROGRESS.value,
            ),
        )

    return events


# ── Tests ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestEventReaderProtocol:
    """EventReader is a runtime-checkable Protocol."""

    def test_stub_implements_protocol(self) -> None:
        reader = StubEventReader()
        assert isinstance(reader, EventReader)


@pytest.mark.unit
class TestReplayResult:
    """ReplayResult model validation."""

    def test_completeness_bounds(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        with pytest.raises(ValidationError):
            ReplayResult(
                context=sample_agent_context,
                replay_completeness=1.5,
                events_processed=0,
                events_total=0,
            )

    def test_valid_construction(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        result = ReplayResult(
            context=sample_agent_context,
            replay_completeness=0.75,
            events_processed=5,
            events_total=7,
        )
        assert result.replay_completeness == 0.75
        assert result.events_processed == 5


@pytest.mark.unit
class TestSessionReplay:
    """Session.replay() reconstructs AgentContext from events."""

    async def test_replay_empty_events(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        reader = StubEventReader()
        result = await Session.replay(
            execution_id=EXEC_ID,
            event_reader=reader,
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        assert result.replay_completeness == 0.0
        assert result.events_processed == 0
        assert result.context.turn_count == 0
        # Execution lineage preserved even with no events.
        assert result.context.execution_id == EXEC_ID

    async def test_replay_full_event_stream(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        events = (
            _event(EXECUTION_ENGINE_START, EXEC_ID, 0),
            _event(
                EXECUTION_CONTEXT_CREATED,
                EXEC_ID,
                1,
                agent_id=str(sample_agent_with_personality.id),
            ),
            _event(EXECUTION_CONTEXT_TURN, EXEC_ID, 2, turn=1, cost=0.01),
            _event(EXECUTION_CONTEXT_TURN, EXEC_ID, 3, turn=2, cost=0.02),
            _event(EXECUTION_CONTEXT_TURN, EXEC_ID, 4, turn=3, cost=0.03),
            _event(
                EXECUTION_TASK_TRANSITION,
                EXEC_ID,
                5,
                task_id=sample_task_with_criteria.id,
                target_status=TaskStatus.IN_PROGRESS.value,
            ),
        )
        reader = StubEventReader(events)
        result = await Session.replay(
            execution_id=EXEC_ID,
            event_reader=reader,
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        assert result.context.turn_count == 3
        assert result.context.accumulated_cost.cost == pytest.approx(0.06)
        assert result.replay_completeness >= 0.85
        assert result.events_processed == 6
        assert result.events_total == 6
        # Execution lineage preserved.
        assert result.context.execution_id == EXEC_ID
        # Task transition replayed.
        assert result.context.task_execution is not None
        assert result.context.task_execution.status == TaskStatus.IN_PROGRESS

    async def test_replay_partial_event_stream(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        events = (
            _event(EXECUTION_CONTEXT_TURN, EXEC_ID, 2, turn=1, cost=0.01),
            _event(EXECUTION_CONTEXT_TURN, EXEC_ID, 3, turn=2, cost=0.02),
        )
        reader = StubEventReader(events)
        result = await Session.replay(
            execution_id=EXEC_ID,
            event_reader=reader,
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        assert result.context.turn_count == 2
        assert result.context.accumulated_cost.cost == pytest.approx(0.03)
        assert 0.3 <= result.replay_completeness <= 0.7
        assert result.events_processed == 2

    async def test_replay_preserves_identity(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        events = (_event(EXECUTION_ENGINE_START, EXEC_ID, 0),)
        reader = StubEventReader(events)
        result = await Session.replay(
            execution_id=EXEC_ID,
            event_reader=reader,
            identity=sample_agent_with_personality,
        )
        assert result.context.identity is sample_agent_with_personality

    async def test_replay_preserves_task(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        events = (_event(EXECUTION_ENGINE_START, EXEC_ID, 0),)
        reader = StubEventReader(events)
        result = await Session.replay(
            execution_id=EXEC_ID,
            event_reader=reader,
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )
        assert result.context.task_execution is not None
        assert result.context.task_execution.task is sample_task_with_criteria

    @pytest.mark.parametrize(
        ("scenario", "expected_min", "expected_max"),
        [
            pytest.param("full", 0.85, 1.0, id="full"),
            pytest.param("turns-only", 0.4, 0.7, id="turns-only"),
            pytest.param("start-only", 0.1, 0.25, id="start-only"),
            pytest.param("empty", 0.0, 0.0, id="empty"),
        ],
    )
    async def test_replay_completeness_scoring(
        self,
        sample_agent_with_personality: AgentIdentity,
        *,
        scenario: str,
        expected_min: float,
        expected_max: float,
    ) -> None:
        events = _build_scenario_events(scenario)
        reader = StubEventReader(tuple(events))
        result = await Session.replay(
            execution_id=EXEC_ID,
            event_reader=reader,
            identity=sample_agent_with_personality,
        )
        assert expected_min <= result.replay_completeness <= expected_max, (
            f"completeness={result.replay_completeness:.2f} "
            f"not in [{expected_min}, {expected_max}]"
        )


@pytest.mark.unit
class TestSessionReplayErrorHandling:
    """Error handling in Session.replay()."""

    async def test_event_reader_exception_propagates(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """EventReader exception is logged and re-raised."""

        class FailingReader:
            async def read_events(
                self,
                execution_id: str,
            ) -> tuple[SessionEvent, ...]:
                msg = "connection failed"
                raise OSError(msg)

        with pytest.raises(OSError, match="connection failed"):
            await Session.replay(
                execution_id=EXEC_ID,
                event_reader=FailingReader(),
                identity=sample_agent_with_personality,
            )

    @pytest.mark.parametrize(
        ("data", "error_id"),
        [
            pytest.param(
                {"turn": "not-a-number", "cost": 0.01},
                "non_numeric_turn",
                id="non_numeric_turn",
            ),
            pytest.param(
                {"cost": 0.01},
                "missing_turn",
                id="missing_turn_key",
            ),
            pytest.param(
                {"turn": 0, "cost": 0.01},
                "zero_turn",
                id="zero_turn_number",
            ),
            pytest.param(
                {"turn": 1, "cost": "invalid"},
                "non_numeric_cost",
                id="non_numeric_cost",
            ),
        ],
    )
    async def test_malformed_turn_event_skipped(
        self,
        sample_agent_with_personality: AgentIdentity,
        data: dict[str, object],
        error_id: str,
    ) -> None:
        """Malformed turn event is skipped; valid events processed."""
        events = (
            _event(EXECUTION_ENGINE_START, EXEC_ID, 0),
            SessionEvent(
                event_name=EXECUTION_CONTEXT_TURN,
                timestamp=_ts(1),
                execution_id=EXEC_ID,
                data=data,
            ),
            _event(
                EXECUTION_CONTEXT_TURN,
                EXEC_ID,
                2,
                turn=2,
                cost=0.02,
            ),
        )
        reader = StubEventReader(events)
        result = await Session.replay(
            execution_id=EXEC_ID,
            event_reader=reader,
            identity=sample_agent_with_personality,
        )
        assert result.events_processed == 3
        assert result.context.turn_count == 1
        assert result.context.accumulated_cost.cost == pytest.approx(
            0.02,
        )


@pytest.mark.unit
class TestSessionReplayTaskNone:
    """Session.replay() with task=None."""

    async def test_replay_without_task(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Replay works when task is None (no task_execution)."""
        events = (
            _event(EXECUTION_ENGINE_START, EXEC_ID, 0),
            _event(EXECUTION_CONTEXT_TURN, EXEC_ID, 1, turn=1, cost=0.01),
        )
        reader = StubEventReader(events)
        result = await Session.replay(
            execution_id=EXEC_ID,
            event_reader=reader,
            identity=sample_agent_with_personality,
            task=None,
        )
        assert result.context.task_execution is None
        assert result.context.turn_count == 1
        assert result.context.accumulated_cost.cost == pytest.approx(0.01)
        assert result.context.execution_id == EXEC_ID


@pytest.mark.unit
class TestSessionReplayExecutionLineage:
    """Execution lineage preservation during replay."""

    async def test_started_at_from_earliest_event(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Replayed context started_at comes from earliest event."""
        events = (
            _event(EXECUTION_ENGINE_START, EXEC_ID, 5),
            _event(EXECUTION_CONTEXT_TURN, EXEC_ID, 10, turn=1, cost=0.01),
        )
        reader = StubEventReader(events)
        result = await Session.replay(
            execution_id=EXEC_ID,
            event_reader=reader,
            identity=sample_agent_with_personality,
        )
        assert result.context.started_at == _ts(5)
