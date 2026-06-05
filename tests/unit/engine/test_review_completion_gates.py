"""Unit tests for the shared completion-gate chain.

Covers the chain-level control flow of ``run_completion_gates`` in
isolation from the full review-gate service: the already-rejected
short-circuit, and the "gate attached but input builder unwired" path
(for example a boot with no persistence) where the gate must stay inert
rather than fail-closed and block every completion.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import Priority, Stakes, TaskStatus, TaskType
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.engine._review_completion_gates import run_completion_gates
from synthorg.engine.review_gate_inputs import DeliverableReviewInputBuilder
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_REVIEW_COMPLETED,
)
from synthorg.security.redteam.models import RedTeamVerdict
from synthorg.security.redteam.protocol import RedTeamGate
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _task(*, stakes: Stakes = Stakes.NORMAL) -> Task:
    return Task(
        id="task-1",
        title="Service",
        description="A development task.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="alice",
        assigned_to="agent-backend",
        status=TaskStatus.IN_REVIEW,
        stakes=stakes,
        acceptance_criteria=(
            AcceptanceCriterion(description="Login endpoint exposed."),
        ),
    )


async def test_rejection_short_circuits_without_evaluating_gates() -> None:
    """An incoming rejection returns unchanged and never touches a gate."""
    gate = mock_of[RedTeamGate](evaluate=AsyncMock())

    target, reason, event, approved = await run_completion_gates(
        red_team_gate=gate,
        vision_gate=None,
        red_team_input_builder=None,
        on_missing_deliverable="block",
        task=_task(),
        target=TaskStatus.IN_PROGRESS,
        transition_reason="rejected upstream",
        event="evt",
        approved=False,
        vision_input=None,
        red_team_min_stakes=Stakes.HIGH,
    )

    assert (target, reason, event, approved) == (
        TaskStatus.IN_PROGRESS,
        "rejected upstream",
        "evt",
        False,
    )
    gate.evaluate.assert_not_awaited()


async def test_gate_without_input_builder_is_inert() -> None:
    """A gate attached without an input builder passes the completion.

    This is the no-persistence boot: the gate is attached (for the
    receipt store) but the flight-recorder deliverable source is absent,
    so there is no builder. The completion must proceed rather than block
    on an un-inspectable deliverable the operator never configured.
    """
    gate = mock_of[RedTeamGate](evaluate=AsyncMock())

    target, _reason, _event, approved = await run_completion_gates(
        red_team_gate=gate,
        vision_gate=None,
        red_team_input_builder=None,
        on_missing_deliverable="block",
        task=_task(),
        target=TaskStatus.COMPLETED,
        transition_reason="approved",
        event=APPROVAL_GATE_REVIEW_COMPLETED,
        approved=True,
        vision_input=None,
        red_team_min_stakes=Stakes.HIGH,
    )

    assert (target, approved) == (TaskStatus.COMPLETED, True)
    gate.evaluate.assert_not_awaited()


async def test_below_stakes_threshold_skips_red_team_gate() -> None:
    """A wired gate does not fire when the task is below the stakes floor.

    The adversarial review is reserved for work at or above
    ``red_team_min_stakes`` (default HIGH), matching the routing layer that
    only marks ``red_team_required`` at that threshold. A NORMAL-stakes
    approval completes without the gate (or its input builder) running.
    """
    gate = mock_of[RedTeamGate](evaluate=AsyncMock())
    builder = mock_of[DeliverableReviewInputBuilder](build=AsyncMock())

    target, _reason, _event, approved = await run_completion_gates(
        red_team_gate=gate,
        vision_gate=None,
        red_team_input_builder=builder,
        on_missing_deliverable="block",
        task=_task(stakes=Stakes.NORMAL),
        target=TaskStatus.COMPLETED,
        transition_reason="approved",
        event=APPROVAL_GATE_REVIEW_COMPLETED,
        approved=True,
        vision_input=None,
        red_team_min_stakes=Stakes.HIGH,
    )

    assert (target, approved) == (TaskStatus.COMPLETED, True)
    gate.evaluate.assert_not_awaited()
    builder.build.assert_not_awaited()


@pytest.mark.parametrize("stakes", [Stakes.HIGH, Stakes.CRITICAL])
async def test_at_or_above_stakes_threshold_runs_red_team_gate(
    stakes: Stakes,
) -> None:
    """The gate fires for work at or above the configured stakes floor.

    A PASS verdict leaves the COMPLETED transition unchanged; the point of
    the test is that the gate (and its input builder) were actually invoked
    for HIGH and CRITICAL stakes.
    """
    gate = mock_of[RedTeamGate](
        evaluate=AsyncMock(return_value=SimpleNamespace(verdict=RedTeamVerdict.PASS)),
    )
    builder = mock_of[DeliverableReviewInputBuilder](
        build=AsyncMock(return_value=SimpleNamespace(execution_id="exec-1")),
    )

    target, _reason, _event, approved = await run_completion_gates(
        red_team_gate=gate,
        vision_gate=None,
        red_team_input_builder=builder,
        on_missing_deliverable="block",
        task=_task(stakes=stakes),
        target=TaskStatus.COMPLETED,
        transition_reason="approved",
        event=APPROVAL_GATE_REVIEW_COMPLETED,
        approved=True,
        vision_input=None,
        red_team_min_stakes=Stakes.HIGH,
    )

    assert (target, approved) == (TaskStatus.COMPLETED, True)
    gate.evaluate.assert_awaited_once()
    builder.build.assert_awaited_once()
