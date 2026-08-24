"""Tests for best-effort AG-UI progress projection (engine -> hub)."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.communication.event_stream.types import AgUiEventType
from synthorg.core.agent import AgentIdentity
from synthorg.engine._stream_progress import (
    make_turn_observer,
    publish_run_started,
    publish_run_terminated,
)
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import TerminationReason, TurnProgress
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _hub(publish: AsyncMock) -> EventStreamHub:
    hub: EventStreamHub = mock_of[EventStreamHub](publish_raw=publish)
    return hub


async def test_run_started_projects_run_started_keyed_by_task() -> None:
    publish = AsyncMock()
    await publish_run_started(_hub(publish), task_id="task-1", agent_id="agent-x")
    publish.assert_awaited_once()
    assert publish.await_args is not None
    kwargs = publish.await_args.kwargs
    assert kwargs["session_id"] == "task-1"
    assert kwargs["event_type"] is AgUiEventType.RUN_STARTED
    assert kwargs["agent_id"] == "agent-x"


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (TerminationReason.COMPLETED, AgUiEventType.RUN_FINISHED),
        (TerminationReason.NO_OP, AgUiEventType.RUN_FINISHED),
        (TerminationReason.ERROR, AgUiEventType.RUN_ERROR),
        # A stuck/exhausted run ends without resuming, so it must surface a
        # terminal frame (RUN_ERROR) or the live panel hangs on "Working".
        (TerminationReason.MAX_TURNS, AgUiEventType.RUN_ERROR),
        (TerminationReason.BUDGET_EXHAUSTED, AgUiEventType.RUN_ERROR),
        (TerminationReason.STAGNATION, AgUiEventType.RUN_ERROR),
    ],
)
async def test_run_terminated_maps_reason_to_event(
    reason: TerminationReason, expected: AgUiEventType
) -> None:
    publish = AsyncMock()
    await publish_run_terminated(
        _hub(publish), task_id="t", agent_id="a", reason=reason
    )
    assert publish.await_args is not None
    assert publish.await_args.kwargs["event_type"] is expected


@pytest.mark.parametrize(
    "reason",
    [TerminationReason.SHUTDOWN, TerminationReason.PARKED, TerminationReason.CANCELLED],
)
async def test_run_terminated_is_noop_for_non_terminal_reasons(
    reason: TerminationReason,
) -> None:
    publish = AsyncMock()
    await publish_run_terminated(
        _hub(publish), task_id="t", agent_id="a", reason=reason
    )
    publish.assert_not_awaited()


async def test_turn_observer_projects_tool_call_with_turn_and_tools(
    sample_agent: AgentIdentity,
) -> None:
    publish = AsyncMock()
    observer = make_turn_observer(_hub(publish), task_id="task-1", agent_id="agent-x")
    await observer(
        TurnProgress(
            3,
            ("search", "read_file"),
            AgentContext.from_identity(sample_agent),
        )
    )
    assert publish.await_args is not None
    kwargs = publish.await_args.kwargs
    assert kwargs["event_type"] is AgUiEventType.TOOL_CALL_START
    assert kwargs["session_id"] == "task-1"
    assert kwargs["payload"]["turn"] == 3
    assert kwargs["payload"]["tools"] == ["search", "read_file"]


async def test_projection_swallows_a_failing_hub() -> None:
    publish = AsyncMock(side_effect=RuntimeError("hub down"))
    # Best-effort: a publish failure must never propagate into execution.
    await publish_run_started(_hub(publish), task_id="t", agent_id="a")


async def test_projection_propagates_cancellation() -> None:
    publish = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await publish_run_started(_hub(publish), task_id="t", agent_id="a")
