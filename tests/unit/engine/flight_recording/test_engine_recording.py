"""AgentEngine auto-records flight-recorder frames through the run hook.

Proves the recording integration: a real ``AgentEngine.run`` (not a
pre-seeded repo) drives the flight-recorder sink so a completed run is
replayable afterwards. Complements the deterministic service-level e2e.
"""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from tests._shared import mock_of
from tests.unit.api.fakes import FakeFlightRecorderFrameRepository

from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.context import AgentContext
from synthorg.engine.flight_recording import PersistenceFlightRecorderSink
from synthorg.engine.loop_protocol import (
    ExecutionLoop,
    ExecutionResult,
    TerminationReason,
    TurnRecord,
)
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrameFilterSpec,
)
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import ChatMessage

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
