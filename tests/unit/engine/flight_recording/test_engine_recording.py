"""AgentEngine auto-records flight-recorder frames through the run hook.

Proves the recording integration: a real ``AgentEngine.run`` (not a
pre-seeded repo) drives the flight-recorder sink so a completed run is
replayable afterwards. Complements the deterministic service-level e2e.

Also pins WHEN the frames land. The completion review asks the frame store
what an attempt delivered, so the frames of the attempt under review have to
be there before the review runs; recorded afterwards, every first run reads
as having delivered nothing and fails a fail-closed gate on an absence the
engine created itself.
"""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.completion_enums import FinishReason
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.context import AgentContext
from synthorg.engine.flight_recording import PersistenceFlightRecorderSink
from synthorg.engine.loop_protocol import (
    ExecutionLoop,
    ExecutionResult,
    TerminationReason,
)
from synthorg.engine.review.models import PipelineResult, ReviewVerdict
from synthorg.engine.review.pipeline import ReviewPipeline
from synthorg.engine.review_gate import ReviewGateService, ReviewRun
from synthorg.engine.task_engine import TaskEngine, TaskMutationResult
from synthorg.execution.turn import TurnRecord
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrameFilterSpec,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage
from tests._shared import mock_of
from tests.unit.api.fakes import FakeFlightRecorderFrameRepository

if TYPE_CHECKING:
    from tests.unit.engine.conftest import MockCompletionProvider

pytestmark = pytest.mark.unit


async def test_run_records_replayable_frames(
    sample_agent_with_personality: AgentIdentity,
    sample_task_with_criteria: Task,
    mock_provider_factory: type[MockCompletionProvider],
) -> None:
    repo = FakeFlightRecorderFrameRepository()

    ctx = AgentContext.from_identity(
        sample_agent_with_personality,
        task=sample_task_with_criteria,
    )
    ctx = ctx.model_copy(
        update={
            "conversation": (
                *ctx.conversation,
                ChatMessage(role=MessageRole.ASSISTANT, content="did the work"),
            ),
        },
    )
    mock_result = ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.MAX_TURNS,
        turns=(
            TurnRecord(
                turn_number=1,
                input_tokens=10,
                output_tokens=5,
                cost=0.01,
                finish_reason=FinishReason.STOP,
            ),
        ),
    )
    mock_loop = mock_of[ExecutionLoop](
        execute=AsyncMock(return_value=mock_result),
        get_loop_type=MagicMock(return_value="react"),
    )
    engine = AgentEngine(
        provider=mock_provider_factory([]),
        execution_loop=mock_loop,
        flight_recorder_sink=PersistenceFlightRecorderSink(repo),
    )

    result = await engine.run(
        identity=sample_agent_with_personality,
        task=sample_task_with_criteria,
    )

    execution_id = result.execution_result.context.execution_id
    frames = await repo.query(
        FlightRecorderFrameFilterSpec(execution_id=execution_id),
    )
    assert len(frames) == 1
    assert frames[0].response_summary == "did the work"
    assert frames[0].turn_index == 1


async def test_the_review_sees_the_frames_of_the_attempt_it_judges(
    sample_agent_with_personality: AgentIdentity,
    sample_task_with_criteria: Task,
    mock_provider_factory: type[MockCompletionProvider],
) -> None:
    """The completion review reads the frame store, so it has to be written first.

    The review's deliverable comes from the frames of the run it is judging.
    Recorded after the review had ruled, the store answered "nothing
    delivered" for every first run, and a fail-closed gate correctly refused
    work that had in fact been done.
    """
    repo = FakeFlightRecorderFrameRepository()
    frames_visible_to_review: list[int] = []

    async def _review(**_kwargs: object) -> ReviewRun:
        frames_visible_to_review.append(
            len(await repo.query(FlightRecorderFrameFilterSpec()))
        )
        return ReviewRun(
            result=PipelineResult(
                task_id=NotBlankStr(str(sample_task_with_criteria.id)),
                final_verdict=ReviewVerdict.PASS,
            ),
            outcome=None,
        )

    ctx = AgentContext.from_identity(
        sample_agent_with_personality,
        task=sample_task_with_criteria,
    )
    ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
    ctx = ctx.model_copy(
        update={
            "conversation": (
                *ctx.conversation,
                ChatMessage(role=MessageRole.ASSISTANT, content="shipped the module"),
            ),
        },
    )
    mock_result = ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.COMPLETED,
        turns=(
            TurnRecord(
                turn_number=1,
                input_tokens=10,
                output_tokens=5,
                cost=0.01,
                finish_reason=FinishReason.STOP,
                tool_calls_made=("write_file",),
            ),
        ),
    )
    engine = AgentEngine(
        provider=mock_provider_factory([]),
        execution_loop=mock_of[ExecutionLoop](
            execute=AsyncMock(return_value=mock_result),
            get_loop_type=MagicMock(return_value="react"),
        ),
        flight_recorder_sink=PersistenceFlightRecorderSink(repo),
        task_engine=mock_of[TaskEngine](
            submit=AsyncMock(
                return_value=TaskMutationResult(
                    request_id="test",
                    success=True,
                    version=1,
                )
            )
        ),
        review_gate=mock_of[ReviewGateService](
            run_pipeline=AsyncMock(side_effect=_review)
        ),
        review_pipeline=mock_of[ReviewPipeline](),
    )

    await engine.run(
        identity=sample_agent_with_personality,
        task=sample_task_with_criteria,
    )

    assert frames_visible_to_review == [1]
