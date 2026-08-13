"""Committing a review decision's transition, including the BLOCKED bridge.

Covered here rather than only through ``ReviewGateService`` because the walk
is the piece with more than one hop, and every assertion reaching it through
the service was ``assert_awaited_once``: a single-hop shape that a two-hop
walk satisfies vacuously by being counted once per call.
"""

from typing import cast
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from synthorg.core.task import Task
from synthorg.core.task_enums import (
    BlockedReason,
    Complexity,
    Priority,
    TaskStatus,
    TaskType,
)
from synthorg.engine._review_gate_transition import commit_decision_transition
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_REVIEW_TRANSITION_FAILED,
)
from tests._shared import as_uuid, mock_of

pytestmark = pytest.mark.unit


def _task(status: TaskStatus) -> Task:
    return Task(
        id=as_uuid("task-1"),
        title="Service",
        description="A development task.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="alice",
        assigned_to="agent-backend",
        status=status,
        estimated_complexity=Complexity.SIMPLE,
    )


def _hops(engine: TaskEngine) -> list[TaskStatus]:
    """Return the statuses the engine was asked for, in order.

    Returns:
        One entry per ``transition_task`` await, in call order.
    """
    transition = cast(AsyncMock, engine.transition_task)
    return [call.args[1] for call in transition.await_args_list]


async def test_a_blocked_task_rejoins_review_before_its_decided_status() -> None:
    """The human's answer goes back through the review it came from.

    COMPLETED is reachable only from IN_REVIEW, so a decision on a task the
    escalation parked has to walk the bridge rather than jump. Asserting the
    ORDER matters as much as the count: arriving at COMPLETED first would
    mean the completion oracle's chokepoint was bypassed.
    """
    engine = mock_of[TaskEngine](transition_task=AsyncMock())

    await commit_decision_transition(
        engine,
        task=_task(TaskStatus.BLOCKED),
        target=TaskStatus.COMPLETED,
        transition_reason="approved by the human the escalation asked for",
        decided_by="alice",
        approval_id="appr-1",
    )

    assert _hops(engine) == [TaskStatus.IN_REVIEW, TaskStatus.COMPLETED]


async def test_the_bridge_hop_names_the_escalation_as_the_reason() -> None:
    """A hop INTO blocked stamps why, so the next reader is not guessing.

    Without it the gate that reads the reason has nothing to read, and falls
    back to the status, which several unrelated paths also produce.
    """
    engine = mock_of[TaskEngine](transition_task=AsyncMock())

    await commit_decision_transition(
        engine,
        task=_task(TaskStatus.IN_REVIEW),
        target=TaskStatus.BLOCKED,
        transition_reason="completion review escalated to a human decision",
        decided_by="alice",
        approval_id="appr-1",
    )

    transition = cast(AsyncMock, engine.transition_task)
    transition.assert_awaited_once()
    kwargs = transition.await_args_list[0].kwargs
    assert kwargs["blocked_reason"] is BlockedReason.ORACLE_ESCALATED


async def test_an_ordinary_decision_takes_exactly_one_hop() -> None:
    """Only the BLOCKED bridge is walked.

    Any other status transitions directly, so a decision on a task that
    should never have reached this gate still raises the engine's own error
    naming the illegal edge, rather than being marched through the lifecycle
    until it becomes legal.
    """
    engine = mock_of[TaskEngine](transition_task=AsyncMock())

    await commit_decision_transition(
        engine,
        task=_task(TaskStatus.IN_REVIEW),
        target=TaskStatus.COMPLETED,
        transition_reason="approved",
        decided_by="alice",
        approval_id="appr-1",
    )

    assert _hops(engine) == [TaskStatus.COMPLETED]


async def test_a_decision_landing_where_the_task_already_is_does_nothing() -> None:
    """Not a conflict: the decision asks for a state the task is in."""
    engine = mock_of[TaskEngine](transition_task=AsyncMock())

    committed = await commit_decision_transition(
        engine,
        task=_task(TaskStatus.IN_PROGRESS),
        target=TaskStatus.IN_PROGRESS,
        transition_reason="rework requested",
        decided_by="alice",
        approval_id="appr-1",
    )

    cast(AsyncMock, engine.transition_task).assert_not_awaited()
    assert committed is False


async def test_a_decision_that_moved_the_task_says_so() -> None:
    """The caller distinguishes deciding from causing.

    A reject always targets IN_PROGRESS, so a task another actor already
    reworked takes the skip above. Both cases file the same decision record,
    which is right (the human did decide), but reading that record afterwards
    it was impossible to tell whether this decision moved the task or merely
    agreed with where it already was, and the difference is the whole question
    when two actors are working the same task.
    """
    engine = mock_of[TaskEngine](transition_task=AsyncMock())

    committed = await commit_decision_transition(
        engine,
        task=_task(TaskStatus.IN_REVIEW),
        target=TaskStatus.IN_PROGRESS,
        transition_reason="rework requested",
        decided_by="alice",
        approval_id="appr-1",
    )

    assert committed is True


async def test_a_failed_second_hop_surfaces_rather_than_reporting_success() -> None:
    """The walk is two engine round-trips, so it can fail between them.

    It must raise: ``_apply_decision`` records the decision only after this
    returns, so swallowing here would file a decision saying the task moved
    somewhere it did not, and leave it parked at the intermediate status
    with the audit trail disagreeing.
    """
    engine = mock_of[TaskEngine](
        transition_task=AsyncMock(side_effect=[None, RuntimeError("queue full")])
    )

    with pytest.raises(RuntimeError, match="queue full"):
        await commit_decision_transition(
            engine,
            task=_task(TaskStatus.BLOCKED),
            target=TaskStatus.COMPLETED,
            transition_reason="approved",
            decided_by="alice",
            approval_id="appr-1",
        )

    assert _hops(engine) == [TaskStatus.IN_REVIEW, TaskStatus.COMPLETED]


@pytest.mark.parametrize(
    ("side_effect", "expected_hop"),
    [
        ([RuntimeError("queue full")], TaskStatus.IN_REVIEW),
        ([None, RuntimeError("queue full")], TaskStatus.COMPLETED),
    ],
)
async def test_the_failure_log_names_the_hop_that_failed(
    side_effect: list[object],
    expected_hop: TaskStatus,
) -> None:
    """Where the walk stopped decides what the task is now.

    Failing the bridge leaves it BLOCKED; failing the second hop leaves it
    parked at IN_REVIEW. Both logged only the final target, so the two states
    an operator would have to recover differently read identically.
    """
    engine = mock_of[TaskEngine](transition_task=AsyncMock(side_effect=side_effect))

    with structlog.testing.capture_logs() as logs, pytest.raises(RuntimeError):
        await commit_decision_transition(
            engine,
            task=_task(TaskStatus.BLOCKED),
            target=TaskStatus.COMPLETED,
            transition_reason="approved",
            decided_by="alice",
            approval_id="appr-1",
        )

    failures = [
        log
        for log in logs
        if log.get("event") == APPROVAL_GATE_REVIEW_TRANSITION_FAILED
    ]
    assert [log["failed_at_status"] for log in failures] == [expected_hop.value]
