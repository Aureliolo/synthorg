"""Unit tests for the flight-recorder sink and frame builder."""

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.enums import TaskStatus
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.engine.flight_recording import (
    NoOpFlightRecorderSink,
    PersistenceFlightRecorderSink,
    build_flight_recorder_sink,
    build_frames,
)
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.execution.turn import TurnRecord
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import ChatMessage
from tests.unit.api.fakes import FakeFlightRecorderFrameRepository

pytestmark = pytest.mark.unit


@pytest.fixture
def agent_context(
    sample_agent_with_personality: AgentIdentity,
    sample_task_with_criteria: Task,
) -> AgentContext:
    return AgentContext.from_identity(
        sample_agent_with_personality,
        task=sample_task_with_criteria,
    )


def _turn(turn_number: int, *, tools: tuple[str, ...] = ()) -> TurnRecord:
    return TurnRecord(
        turn_number=turn_number,
        input_tokens=10,
        output_tokens=5,
        cost=0.001,
        tool_calls_made=tools,
        finish_reason=FinishReason.TOOL_USE if tools else FinishReason.STOP,
    )


def _result(
    identity_context: AgentContext,
    *,
    turns: tuple[TurnRecord, ...],
    reason: TerminationReason = TerminationReason.COMPLETED,
) -> ExecutionResult:
    return ExecutionResult(
        context=identity_context,
        termination_reason=reason,
        turns=turns,
    )


def _context_with_replies(base: AgentContext, replies: list[str]) -> AgentContext:
    messages = tuple(
        ChatMessage(role=MessageRole.ASSISTANT, content=text) for text in replies
    )
    return base.model_copy(update={"conversation": (*base.conversation, *messages)})


class TestBuildFrames:
    def test_one_frame_per_turn_with_content(self, agent_context: AgentContext) -> None:
        ctx = _context_with_replies(agent_context, ["first", "done"])
        result = _result(
            ctx,
            turns=(_turn(1, tools=("search",)), _turn(2)),
        )

        frames = build_frames(
            result,
            execution_id="exec-1",
            agent_id="agent-1",
            task_id="task-1",
        )

        assert [f.turn_index for f in frames] == [1, 2]
        assert frames[0].decision == "tool_call"
        assert frames[0].tool_calls == ("search",)
        assert frames[0].response_summary == "first"
        assert frames[0].status is TaskStatus.IN_PROGRESS
        # Terminal turn carries the run outcome.
        assert frames[1].decision == "completed"
        assert frames[1].status is TaskStatus.COMPLETED

    def test_failed_run_terminal_status(self, agent_context: AgentContext) -> None:
        ctx = _context_with_replies(agent_context, ["boom"])
        result = ExecutionResult(
            context=ctx,
            termination_reason=TerminationReason.ERROR,
            turns=(_turn(1),),
            error_message="boom",
        )

        frames = build_frames(
            result,
            execution_id="exec-1",
            agent_id="agent-1",
            task_id=None,
        )
        assert frames[0].status is TaskStatus.FAILED
        assert frames[0].task_id is None

    def test_summary_truncation(self, agent_context: AgentContext) -> None:
        ctx = _context_with_replies(agent_context, ["x" * 50])
        result = _result(ctx, turns=(_turn(1),))

        frames = build_frames(
            result,
            execution_id="exec-1",
            agent_id="agent-1",
            task_id="task-1",
            summary_max_chars=10,
        )
        assert frames[0].response_summary == "x" * 10

    def test_no_turns_yields_no_frames(self, agent_context: AgentContext) -> None:
        result = _result(agent_context, turns=())
        frames = build_frames(
            result,
            execution_id="exec-1",
            agent_id="agent-1",
            task_id="task-1",
        )
        assert frames == ()


class TestSinks:
    async def test_persistence_sink_appends(self, agent_context: AgentContext) -> None:
        repo = FakeFlightRecorderFrameRepository()
        sink = PersistenceFlightRecorderSink(repo)
        ctx = _context_with_replies(agent_context, ["a", "b"])
        frames = build_frames(
            _result(ctx, turns=(_turn(1), _turn(2))),
            execution_id="exec-1",
            agent_id="agent-1",
            task_id="task-1",
        )

        await sink.record_frames(frames)

        from synthorg.persistence.flight_recorder_protocol import (
            FlightRecorderFrameFilterSpec,
        )

        stored = await repo.query(
            FlightRecorderFrameFilterSpec(execution_id="exec-1"),
        )
        assert len(stored) == 2

    async def test_persistence_sink_swallows_failure(
        self, agent_context: AgentContext
    ) -> None:
        repo = FakeFlightRecorderFrameRepository()
        sink = PersistenceFlightRecorderSink(repo)
        ctx = _context_with_replies(agent_context, ["a"])
        frames = build_frames(
            _result(ctx, turns=(_turn(1),)),
            execution_id="exec-1",
            agent_id="agent-1",
            task_id="task-1",
        )
        # Duplicate ids: the second record_frames call hits DuplicateRecordError
        # for every frame, which the sink must swallow rather than raise.
        await sink.record_frames(frames)
        await sink.record_frames(frames)

    async def test_noop_sink_records_nothing(self, agent_context: AgentContext) -> None:
        sink = NoOpFlightRecorderSink()
        ctx = _context_with_replies(agent_context, ["a"])
        frames = build_frames(
            _result(ctx, turns=(_turn(1),)),
            execution_id="exec-1",
            agent_id="agent-1",
            task_id="task-1",
        )
        await sink.record_frames(frames)  # no-op, must not raise

    def test_factory_selects_noop_when_disabled(self) -> None:
        repo = FakeFlightRecorderFrameRepository()
        assert isinstance(
            build_flight_recorder_sink(repo, enabled=False),
            NoOpFlightRecorderSink,
        )

    def test_factory_selects_noop_for_strategy(self) -> None:
        repo = FakeFlightRecorderFrameRepository()
        assert isinstance(
            build_flight_recorder_sink(repo, strategy="noop"),
            NoOpFlightRecorderSink,
        )

    def test_factory_selects_persistence(self) -> None:
        repo = FakeFlightRecorderFrameRepository()
        assert isinstance(
            build_flight_recorder_sink(repo),
            PersistenceFlightRecorderSink,
        )

    def test_factory_noop_without_repository(self) -> None:
        assert isinstance(
            build_flight_recorder_sink(None),
            NoOpFlightRecorderSink,
        )
