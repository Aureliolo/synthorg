"""Tests for the event projector."""

import pytest

from synthorg.communication.event_stream.projector import (
    PROJECTION_MAP,
    project_event,
)
from synthorg.communication.event_stream.types import AgUiEventType
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_CONTEXT_PARKED,
    APPROVAL_GATE_CONTEXT_RESUMED,
)
from synthorg.observability.events.conflict import CONFLICT_DISSENT_RECORDED
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_COMPLETE,
    EXECUTION_ENGINE_ERROR,
    EXECUTION_ENGINE_START,
    EXECUTION_LOOP_TOOL_CALLS,
    EXECUTION_LOOP_TURN_COMPLETE,
    EXECUTION_LOOP_TURN_START,
)


@pytest.mark.unit
class TestProjectionMap:
    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            (EXECUTION_ENGINE_START, AgUiEventType.RUN_STARTED),
            (EXECUTION_ENGINE_COMPLETE, AgUiEventType.RUN_FINISHED),
            (EXECUTION_ENGINE_ERROR, AgUiEventType.RUN_ERROR),
            (EXECUTION_LOOP_TURN_START, AgUiEventType.TEXT_MESSAGE_START),
            (EXECUTION_LOOP_TURN_COMPLETE, AgUiEventType.TEXT_MESSAGE_END),
            (EXECUTION_LOOP_TOOL_CALLS, AgUiEventType.TOOL_CALL_START),
            (APPROVAL_GATE_CONTEXT_PARKED, AgUiEventType.APPROVAL_INTERRUPT),
            (APPROVAL_GATE_CONTEXT_RESUMED, AgUiEventType.APPROVAL_RESUMED),
        ],
    )
    def test_projection_mapping(self, key: str, expected: AgUiEventType) -> None:
        assert PROJECTION_MAP[key] == expected

    def test_dissent_not_in_projection_map(self) -> None:
        # Dissent is emitted directly by ConflictResolutionService
        # via EventStreamHub.publish_raw(), not through projection.
        assert CONFLICT_DISSENT_RECORDED not in PROJECTION_MAP

    def test_no_step_events_are_projected(self) -> None:
        # AG-UI's STEP_* members stay as protocol vocabulary, but no loop
        # emits a step, so a mapping onto them could never fire.
        assert AgUiEventType.STEP_STARTED not in PROJECTION_MAP.values()
        assert AgUiEventType.STEP_FINISHED not in PROJECTION_MAP.values()
        assert AgUiEventType.STEP_FAILED not in PROJECTION_MAP.values()


@pytest.mark.unit
class TestProjectEvent:
    def test_mapped_event_returns_stream_event(self) -> None:
        result = project_event(
            EXECUTION_ENGINE_START,
            session_id="s1",
        )
        assert result is not None
        assert result.type == AgUiEventType.RUN_STARTED
        assert result.session_id == "s1"

    def test_unmapped_event_returns_none(self) -> None:
        result = project_event(
            "some.unknown.internal.event",
            session_id="s1",
        )
        assert result is None

    def test_payload_passed_through(self) -> None:
        result = project_event(
            EXECUTION_ENGINE_START,
            session_id="s1",
            payload={"task_id": "t-1", "agent_id": "a-1"},
        )
        assert result is not None
        assert result.payload["task_id"] == "t-1"

    def test_agent_id_propagated(self) -> None:
        result = project_event(
            EXECUTION_ENGINE_START,
            session_id="s1",
            agent_id="agent-eng-001",
        )
        assert result is not None
        assert result.agent_id == "agent-eng-001"

    def test_correlation_id_propagated(self) -> None:
        result = project_event(
            EXECUTION_ENGINE_START,
            session_id="s1",
            correlation_id="corr-123",
        )
        assert result is not None
        assert result.correlation_id == "corr-123"

    def test_generated_id_is_unique(self) -> None:
        r1 = project_event(EXECUTION_ENGINE_START, session_id="s1")
        r2 = project_event(EXECUTION_ENGINE_START, session_id="s1")
        assert r1 is not None
        assert r2 is not None
        assert r1.id != r2.id
