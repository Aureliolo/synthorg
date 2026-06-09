"""Unit tests for ``DeliverableReviewInputBuilder``.

Verifies the builder sources the red-team review input from the latest
flight-recorder frame and returns ``None`` (so the gate applies its
``on_missing_deliverable`` posture) when no reviewable deliverable
exists.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.engine.review_gate_inputs import DeliverableReviewInputBuilder
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrame,
    FlightRecorderFrameAggregate,
    FlightRecorderFrameRepository,
)
from tests._shared import as_uuid, mock_of

pytestmark = pytest.mark.unit


def _task(
    *,
    assigned_to: str | None = "agent-backend",
    criteria: tuple[AcceptanceCriterion, ...] = (
        AcceptanceCriterion(description="Login endpoint exposed."),
    ),
) -> Task:
    return Task(
        id=as_uuid("task-1"),
        title="Service",
        description="A development task.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="alice",
        assigned_to=assigned_to,
        status=TaskStatus.IN_REVIEW if assigned_to else TaskStatus.CREATED,
        acceptance_criteria=criteria,
    )


def _frame(response: str | None) -> FlightRecorderFrame:
    return FlightRecorderFrame(
        execution_id="exec-9",
        task_id="task-1",
        agent_id="agent-backend",
        turn_index=3,
        response_summary=response,
        status=TaskStatus.COMPLETED,
    )


def _frame_repo(
    *,
    latest_execution_id: str | None,
    frames: tuple[FlightRecorderFrame, ...] = (),
) -> FlightRecorderFrameRepository:
    repo: FlightRecorderFrameRepository = mock_of[FlightRecorderFrameRepository](
        get_aggregate=AsyncMock(
            return_value=FlightRecorderFrameAggregate(
                latest_execution_id=latest_execution_id,
            ),
        ),
        query=AsyncMock(return_value=frames),
    )
    return repo


async def _supervised() -> AutonomyLevel:
    return AutonomyLevel.SUPERVISED


async def test_build_returns_input_for_recorded_deliverable() -> None:
    """The latest frame's response becomes the review input deliverable."""
    repo = _frame_repo(
        latest_execution_id="exec-9",
        frames=(_frame("Deliverable: login endpoint shipped."),),
    )
    builder = DeliverableReviewInputBuilder(
        frame_repository=repo,
        autonomy_provider=_supervised,
    )

    result = await builder.build(_task())

    assert result is not None
    assert result.execution_id == "exec-9"
    assert result.deliverable_content == "Deliverable: login endpoint shipped."
    assert result.acceptance_criteria == ("Login endpoint exposed.",)
    assert result.assigned_agent_id == "agent-backend"
    assert result.autonomy is AutonomyLevel.SUPERVISED


async def test_build_returns_none_when_no_frame() -> None:
    """No recorded execution -> no review input."""
    repo = _frame_repo(latest_execution_id=None)
    builder = DeliverableReviewInputBuilder(
        frame_repository=repo,
        autonomy_provider=_supervised,
    )

    assert await builder.build(_task()) is None


async def test_build_returns_none_when_response_empty() -> None:
    """A terminal frame with no response text -> no review input."""
    repo = _frame_repo(
        latest_execution_id="exec-9",
        frames=(_frame("   "),),
    )
    builder = DeliverableReviewInputBuilder(
        frame_repository=repo,
        autonomy_provider=_supervised,
    )

    assert await builder.build(_task()) is None


async def test_build_returns_none_without_assignee() -> None:
    """A task with no assignee cannot attribute a deliverable."""
    repo = _frame_repo(
        latest_execution_id="exec-9",
        frames=(_frame("anything"),),
    )
    builder = DeliverableReviewInputBuilder(
        frame_repository=repo,
        autonomy_provider=_supervised,
    )

    assert await builder.build(_task(assigned_to=None)) is None


async def test_build_returns_none_without_acceptance_criteria() -> None:
    """A task with no acceptance criteria has nothing to verify against."""
    repo = _frame_repo(
        latest_execution_id="exec-9",
        frames=(_frame("anything"),),
    )
    builder = DeliverableReviewInputBuilder(
        frame_repository=repo,
        autonomy_provider=_supervised,
    )

    assert await builder.build(_task(criteria=())) is None
