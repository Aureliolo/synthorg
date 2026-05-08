"""Integration test: session recovery after simulated brain failure."""

from datetime import UTC, datetime

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.enums import TaskStatus
from synthorg.core.task import Task
from synthorg.engine.session import Session, SessionEvent
from synthorg.observability.events.execution import (
    EXECUTION_CONTEXT_CREATED,
    EXECUTION_CONTEXT_TURN,
    EXECUTION_ENGINE_START,
    EXECUTION_TASK_TRANSITION,
)


class InMemoryEventReader:
    """EventReader that collects events in memory during a simulated run.

    Simulates what an operator's observability backend would store.
    """

    def __init__(self) -> None:
        self._events: list[SessionEvent] = []

    def record(self, event: SessionEvent) -> None:
        """Record an event (simulates the sink writing to storage)."""
        self._events.append(event)

    async def read_events(
        self,
        execution_id: str,
    ) -> tuple[SessionEvent, ...]:
        return tuple(e for e in self._events if e.execution_id == execution_id)


def _ts(minute: int) -> datetime:
    return datetime(2026, 4, 13, 12, minute, 0, tzinfo=UTC)


@pytest.mark.unit
class TestBrainFailureRecovery:
    """Simulate a brain failure and recover via Session.replay()."""

    async def test_recovery_after_simulated_crash(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Simulate: agent runs 3 turns, crashes, replays from events."""
        exec_id = "exec-crash-sim-001"
        store = InMemoryEventReader()

        # Simulate normal execution emitting events.
        store.record(
            SessionEvent(
                event_name=EXECUTION_ENGINE_START,
                timestamp=_ts(0),
                execution_id=exec_id,
            )
        )
        store.record(
            SessionEvent(
                event_name=EXECUTION_CONTEXT_CREATED,
                timestamp=_ts(1),
                execution_id=exec_id,
                data={"agent_id": str(sample_agent_with_personality.id)},
            )
        )
        for turn in range(1, 4):
            store.record(
                SessionEvent(
                    event_name=EXECUTION_CONTEXT_TURN,
                    timestamp=_ts(1 + turn),
                    execution_id=exec_id,
                    data={"turn": turn, "cost": 0.01 * turn},
                )
            )
        store.record(
            SessionEvent(
                event_name=EXECUTION_TASK_TRANSITION,
                timestamp=_ts(5),
                execution_id=exec_id,
                data={"target_status": TaskStatus.IN_PROGRESS.value},
            )
        )
        # Brain crashes here (no EXECUTION_ENGINE_COMPLETE).

        # Recovery via Session.replay().
        result = await Session.replay(
            execution_id=exec_id,
            event_reader=store,
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        # Verify recovered state.
        assert result.context.turn_count == 3
        assert result.context.accumulated_cost.cost == pytest.approx(0.06)
        assert result.replay_completeness >= 0.85
        assert result.events_processed == 6
        assert result.context.identity is sample_agent_with_personality
        assert result.context.task_execution is not None

    async def test_recovery_with_partial_events(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Crash after 1 turn, only turn event survived."""
        exec_id = "exec-crash-sim-002"
        store = InMemoryEventReader()

        store.record(
            SessionEvent(
                event_name=EXECUTION_CONTEXT_TURN,
                timestamp=_ts(2),
                execution_id=exec_id,
                data={"turn": 1, "cost": 0.05},
            )
        )

        result = await Session.replay(
            execution_id=exec_id,
            event_reader=store,
            identity=sample_agent_with_personality,
        )

        assert result.context.turn_count == 1
        assert result.context.accumulated_cost.cost == pytest.approx(0.05)
        assert result.replay_completeness < 0.85
        assert result.replay_completeness > 0.0

    async def test_recovery_unknown_execution_returns_fresh(
        self,
        sample_agent_with_personality: AgentIdentity,
    ) -> None:
        """Unknown execution ID returns fresh context."""
        store = InMemoryEventReader()

        result = await Session.replay(
            execution_id="nonexistent",
            event_reader=store,
            identity=sample_agent_with_personality,
        )

        assert result.context.turn_count == 0
        assert result.replay_completeness == 0.0
