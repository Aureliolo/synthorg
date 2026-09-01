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

from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.completion_enums import FinishReason
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_state import AgentRuntimeState, ExecutionStatus
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
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskMutationResult
from synthorg.execution.turn import TurnRecord
from synthorg.persistence.agent_state_protocol import AgentStateRepository
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrameFilterSpec,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage
from tests._shared import (
    UNWIRED_OBSERVABILITY,
    UNWIRED_ORG,
    engine_with,
    mock_of,
    unwired_core,
    unwired_governance,
)
from tests.unit.api.fakes import FakeFlightRecorderFrameRepository

if TYPE_CHECKING:
    from tests.unit.engine.conftest import MockCompletionProvider

pytestmark = pytest.mark.unit


class _LoopDiedError(RuntimeError):
    """A loop that failed outright, however it failed."""


async def test_run_records_replayable_frames(
    sample_agent: AgentIdentity,
    sample_task_with_criteria: Task,
    mock_provider_factory: type[MockCompletionProvider],
) -> None:
    repo = FakeFlightRecorderFrameRepository()

    ctx = AgentContext.from_identity(
        sample_agent,
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
    engine = engine_with(
        mock_provider_factory([]),
        core=replace(unwired_core(mock_provider_factory([])), execution_loop=mock_loop),
        observability=replace(
            UNWIRED_OBSERVABILITY,
            flight_recorder_sink=PersistenceFlightRecorderSink(repo),
        ),
    )

    result = await engine.run(
        identity=sample_agent,
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
    sample_agent: AgentIdentity,
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
        sample_agent,
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
    engine = engine_with(
        mock_provider_factory([]),
        core=replace(
            unwired_core(mock_provider_factory([])),
            execution_loop=mock_of[ExecutionLoop](
                execute=AsyncMock(return_value=mock_result),
                get_loop_type=MagicMock(return_value="react"),
            ),
        ),
        governance=replace(
            unwired_governance(),
            review_gate=mock_of[ReviewGateService](
                run_pipeline=AsyncMock(side_effect=_review)
            ),
            review_pipeline=mock_of[ReviewPipeline](),
        ),
        org=replace(
            UNWIRED_ORG,
            task_engine=mock_of[TaskEngine](
                submit=AsyncMock(
                    return_value=TaskMutationResult(
                        request_id="test",
                        success=True,
                        version=1,
                    )
                )
            ),
        ),
        observability=replace(
            UNWIRED_OBSERVABILITY,
            flight_recorder_sink=PersistenceFlightRecorderSink(repo),
        ),
    )

    await engine.run(
        identity=sample_agent,
        task=sample_task_with_criteria,
    )

    assert frames_visible_to_review == [1]


async def test_the_review_judges_the_attempt_not_the_recorded_copy(
    sample_agent: AgentIdentity,
    sample_task_with_criteria: Task,
    mock_provider_factory: type[MockCompletionProvider],
) -> None:
    """A recorder that stored nothing must not read as an empty delivery.

    The gate resolves what was delivered from the run it is judging, which
    the engine is holding. Asking the recorder instead gives that question
    two owners, and the second is an observability store: with it empty, a
    completed run is indistinguishable from an agent that produced nothing
    and is sent to rework as empty.
    """
    seen: list[str | None] = []

    async def _review(**kwargs: object) -> ReviewRun:
        attempt = kwargs.get("attempt")
        seen.append(getattr(attempt, "closing_message", None))
        return ReviewRun(
            result=PipelineResult(
                task_id=NotBlankStr(str(sample_task_with_criteria.id)),
                final_verdict=ReviewVerdict.PASS,
            ),
            outcome=None,
        )

    ctx = AgentContext.from_identity(
        sample_agent,
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
    engine = engine_with(
        mock_provider_factory([]),
        core=replace(
            unwired_core(mock_provider_factory([])),
            execution_loop=mock_of[ExecutionLoop](
                execute=AsyncMock(
                    return_value=ExecutionResult(
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
                ),
                get_loop_type=MagicMock(return_value="react"),
            ),
        ),
        governance=replace(
            unwired_governance(),
            review_gate=mock_of[ReviewGateService](
                run_pipeline=AsyncMock(side_effect=_review)
            ),
            review_pipeline=mock_of[ReviewPipeline](),
        ),
        org=replace(
            UNWIRED_ORG,
            task_engine=mock_of[TaskEngine](
                submit=AsyncMock(
                    return_value=TaskMutationResult(
                        request_id="test",
                        success=True,
                        version=1,
                    )
                )
            ),
        ),
    )

    await engine.run(
        identity=sample_agent,
        task=sample_task_with_criteria,
    )

    assert seen == ["shipped the module"]


def _recording_state_repository(
    saved: list[AgentRuntimeState],
) -> AgentStateRepository:
    """A state repository that records both of the engine's write paths.

    The running write is unconditional and the idle one is a compare-and-set,
    so a fake wired to ``save`` alone sees only half the lifecycle.

    Returns:
        The recording repository.
    """

    async def _guarded(state: AgentRuntimeState, **_: object) -> bool:
        saved.append(state)
        return True

    repository: AgentStateRepository = mock_of[AgentStateRepository](
        save=AsyncMock(side_effect=saved.append),
        save_if_execution=AsyncMock(side_effect=_guarded),
    )
    return repository


async def test_the_engine_records_the_agents_live_state(
    sample_agent: AgentIdentity,
    sample_task_with_criteria: Task,
    mock_provider_factory: type[MockCompletionProvider],
) -> None:
    """The wiring itself, not just the helpers it wires.

    ``make_runtime_state_observer`` and ``mark_agent_idle`` are unit-tested
    as functions, so a regression that stops the ENGINE passing them (a
    simplified ``finally``, a dropped ``compose_turn_observers``) would leave
    the cockpit blind again with every one of those tests still green.
    """
    saved: list[AgentRuntimeState] = []
    repository = _recording_state_repository(saved)
    ctx = AgentContext.from_identity(
        sample_agent,
        task=sample_task_with_criteria,
    )
    engine = engine_with(
        mock_provider_factory([]),
        core=replace(
            unwired_core(mock_provider_factory([])),
            execution_loop=mock_of[ExecutionLoop](
                execute=AsyncMock(
                    return_value=ExecutionResult(
                        context=ctx,
                        termination_reason=TerminationReason.COMPLETED,
                    )
                ),
                get_loop_type=MagicMock(return_value="react"),
            ),
        ),
        observability=replace(
            UNWIRED_OBSERVABILITY, agent_state_repository_provider=lambda: repository
        ),
    )

    await engine.run(
        identity=sample_agent,
        task=sample_task_with_criteria,
    )

    assert [state.status for state in saved] == [
        ExecutionStatus.EXECUTING,
        ExecutionStatus.IDLE,
    ]


async def test_a_run_that_died_still_stops_reading_as_busy(
    sample_agent: AgentIdentity,
    sample_task_with_criteria: Task,
    mock_provider_factory: type[MockCompletionProvider],
) -> None:
    """The idle write is in a ``finally`` precisely for this.

    A row left EXECUTING makes a finished agent look occupied for the life
    of the process, and ``get_active`` is the query the live view is built
    on, so the failure would present as an agent that never stops working.
    """
    saved: list[AgentRuntimeState] = []
    repository = _recording_state_repository(saved)
    engine = engine_with(
        mock_provider_factory([]),
        core=replace(
            unwired_core(mock_provider_factory([])),
            execution_loop=mock_of[ExecutionLoop](
                execute=AsyncMock(side_effect=_LoopDiedError),
                get_loop_type=MagicMock(return_value="react"),
            ),
        ),
        observability=replace(
            UNWIRED_OBSERVABILITY, agent_state_repository_provider=lambda: repository
        ),
    )

    result = await engine.run(
        identity=sample_agent,
        task=sample_task_with_criteria,
    )

    # Both halves. Recording IDLE is what stops the agent reading as busy,
    # but on its own it is also what a run that swallowed the death and
    # reported success would produce, and a loop that died reporting success
    # is the failure the fail-loud rule exists to prevent.
    assert result.execution_result.termination_reason is TerminationReason.ERROR
    assert result.execution_result.error_type == _LoopDiedError.__name__
    assert saved[-1].status is ExecutionStatus.IDLE
