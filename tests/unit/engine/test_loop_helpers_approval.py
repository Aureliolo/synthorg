"""Tests for approval gate integration in loop helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.approval.models import EscalationInfo
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.loop_tool_execution import execute_tool_calls
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import (
    ZERO_TOKEN_USAGE,
    CompletionResponse,
    ToolCall,
    ToolResult,
)
from tests._shared import mock_of
from tests.unit.engine.approval_helpers import make_escalation as _make_escalation

pytestmark = pytest.mark.unit


def _make_response_with_tool_calls() -> CompletionResponse:
    return CompletionResponse(
        content="I'll use the tool",
        finish_reason=FinishReason.TOOL_USE,
        tool_calls=(ToolCall(id="tc-1", name="stub_tool", arguments={}),),
        usage=ZERO_TOKEN_USAGE,
        model="test-small-001",
    )


def _make_context() -> MagicMock:
    ctx = MagicMock()
    ctx.execution_id = "exec-1"
    ctx.turn_count = 1
    ctx.with_message.return_value = ctx
    ctx.accumulated_cost = MagicMock()
    return ctx


def _make_tool_invoker(
    *,
    escalations: tuple[EscalationInfo, ...] = (),
) -> MagicMock:
    invoker = MagicMock()
    invoker.invoke_all = AsyncMock(
        return_value=(ToolResult(tool_call_id="tc-1", content="ok", is_error=False),),
    )
    invoker.pending_escalations = escalations
    return invoker


class TestParkedConversationShape:
    """The parked conversation already answers the escalated tool call.

    Load-bearing invariant for the resume-injection design: the loop
    appends the TOOL result message for the escalated call *before*
    the park check, so the parked conversation has no dangling
    unanswered tool call. Resume therefore injects the decision as a
    follow-up SYSTEM message (``ApprovalGate.build_resume_message``),
    not a second ToolResult for the same ``tool_call_id`` (which would
    duplicate it and malform the message stream). If a refactor moves
    the park check before the tool-result append, this test fails and
    the resume-injection strategy must be revisited.
    """

    async def test_parked_context_last_message_is_tool_result(self) -> None:
        from synthorg.core.enums import TaskStatus
        from synthorg.engine.context import AgentContext
        from synthorg.providers.enums import MessageRole

        from .conftest import make_assignment_agent, make_assignment_task

        identity = make_assignment_agent("test-agent")
        task = make_assignment_task(
            id="task-1",
            assigned_to="test-agent",
            status=TaskStatus.IN_PROGRESS,
        )
        ctx = AgentContext.from_identity(identity, task=task)
        escalation = _make_escalation()
        invoker = _make_tool_invoker(escalations=(escalation,))
        response = _make_response_with_tool_calls()

        captured: dict[str, AgentContext] = {}

        async def _capture_park(**kwargs: object) -> MagicMock:
            captured["ctx"] = kwargs["context"]  # type: ignore[assignment]
            return MagicMock(id="parked-1")

        gate = mock_of[ApprovalGate](
            should_park=MagicMock(return_value=escalation),
            park_context=AsyncMock(side_effect=_capture_park),
        )

        await execute_tool_calls(
            ctx,
            invoker,
            response,
            1,
            [],
            approval_gate=gate,
        )

        parked_ctx = captured["ctx"]
        last = parked_ctx.conversation[-1]
        assert last.role == MessageRole.TOOL
        assert last.tool_result is not None
        assert last.tool_result.tool_call_id == "tc-1"


class TestExecuteToolCallsNoGate:
    """execute_tool_calls returns AgentContext normally without gate."""

    async def test_returns_context_without_gate(self) -> None:
        ctx = _make_context()
        invoker = _make_tool_invoker()
        response = _make_response_with_tool_calls()

        result = await execute_tool_calls(
            ctx,
            invoker,
            response,
            1,
            [],
        )
        # Should return updated context, not ExecutionResult
        assert not isinstance(result, ExecutionResult)


class TestExecuteToolCallsWithGate:
    """execute_tool_calls with approval gate integration."""

    async def test_no_escalation_returns_context(self) -> None:
        ctx = _make_context()
        invoker = _make_tool_invoker(escalations=())
        response = _make_response_with_tool_calls()
        gate = MagicMock(spec=ApprovalGate)
        gate.should_park.return_value = None

        result = await execute_tool_calls(
            ctx,
            invoker,
            response,
            1,
            [],
            approval_gate=gate,
        )
        assert not isinstance(result, ExecutionResult)
        gate.should_park.assert_called_once()

    @patch("synthorg.engine.loop_helpers.build_result")
    async def test_escalation_returns_parked_result(
        self,
        mock_build_result: MagicMock,
    ) -> None:
        parked_result = MagicMock(spec=ExecutionResult)
        parked_result.termination_reason = TerminationReason.PARKED
        parked_result.metadata = {"approval_id": "approval-1"}
        mock_build_result.return_value = parked_result

        ctx = _make_context()
        escalation = _make_escalation()
        invoker = _make_tool_invoker(escalations=(escalation,))
        response = _make_response_with_tool_calls()

        gate = MagicMock(spec=ApprovalGate)
        gate.should_park.return_value = escalation
        gate.park_context = AsyncMock(return_value=MagicMock(id="parked-1"))

        result = await execute_tool_calls(
            ctx,
            invoker,
            response,
            1,
            [],
            approval_gate=gate,
        )
        assert result is parked_result
        mock_build_result.assert_called_once()
        call_kwargs = mock_build_result.call_args
        assert call_kwargs[0][1] == TerminationReason.PARKED
        assert call_kwargs[1]["metadata"]["approval_id"] == "approval-1"

    @patch("synthorg.engine.loop_helpers.build_result")
    async def test_parked_result_has_approval_id_in_metadata(
        self,
        mock_build_result: MagicMock,
    ) -> None:
        parked_result = MagicMock(spec=ExecutionResult)
        parked_result.termination_reason = TerminationReason.PARKED
        parked_result.metadata = {"approval_id": "approval-xyz"}
        mock_build_result.return_value = parked_result

        ctx = _make_context()
        escalation = _make_escalation(approval_id="approval-xyz")
        invoker = _make_tool_invoker(escalations=(escalation,))
        response = _make_response_with_tool_calls()

        gate = MagicMock(spec=ApprovalGate)
        gate.should_park.return_value = escalation
        gate.park_context = AsyncMock(return_value=MagicMock(id="parked-1"))

        result = await execute_tool_calls(
            ctx,
            invoker,
            response,
            1,
            [],
            approval_gate=gate,
        )
        assert result is parked_result
        call_kwargs = mock_build_result.call_args
        assert call_kwargs[1]["metadata"]["approval_id"] == "approval-xyz"

    @patch("synthorg.engine.loop_helpers.build_result")
    async def test_park_failure_returns_error(
        self,
        mock_build_result: MagicMock,
    ) -> None:
        error_result = MagicMock(spec=ExecutionResult)
        error_result.termination_reason = TerminationReason.ERROR
        mock_build_result.return_value = error_result

        ctx = _make_context()
        escalation = _make_escalation()
        invoker = _make_tool_invoker(escalations=(escalation,))
        response = _make_response_with_tool_calls()

        gate = MagicMock(spec=ApprovalGate)
        gate.should_park.return_value = escalation
        gate.park_context = AsyncMock(
            side_effect=ValueError("serialization failed"),
        )

        result = await execute_tool_calls(
            ctx,
            invoker,
            response,
            1,
            [],
            approval_gate=gate,
        )
        assert result is error_result
        mock_build_result.assert_called_once()
        call_kwargs = mock_build_result.call_args
        assert call_kwargs[0][1] == TerminationReason.ERROR
        assert call_kwargs[1]["metadata"]["approval_id"] == "approval-1"
        assert call_kwargs[1]["metadata"]["parking_failed"] is True
        assert "context parking failed" in call_kwargs[1]["error_message"]

    @patch("synthorg.engine.loop_helpers.build_result")
    async def test_park_without_task_execution(
        self,
        mock_build_result: MagicMock,
    ) -> None:
        """Context without task_execution parks with task_id=None."""
        parked_result = MagicMock(spec=ExecutionResult)
        parked_result.termination_reason = TerminationReason.PARKED
        parked_result.metadata = {"approval_id": "approval-1"}
        mock_build_result.return_value = parked_result

        ctx = _make_context()
        ctx.task_execution = None  # No task -- taskless agent
        escalation = _make_escalation()
        invoker = _make_tool_invoker(escalations=(escalation,))
        response = _make_response_with_tool_calls()

        gate = MagicMock(spec=ApprovalGate)
        gate.should_park.return_value = escalation
        gate.park_context = AsyncMock(return_value=MagicMock(id="parked-1"))

        result = await execute_tool_calls(
            ctx,
            invoker,
            response,
            1,
            [],
            approval_gate=gate,
        )
        assert result is parked_result
        # Verify park_context was called with task_id=None
        gate.park_context.assert_called_once()
        call_kwargs = gate.park_context.call_args
        assert call_kwargs.kwargs.get("task_id") is None

    @patch("synthorg.engine.loop_helpers.build_result")
    async def test_park_failure_with_io_error(
        self,
        mock_build_result: MagicMock,
    ) -> None:
        """park_context raising IOError returns ERROR result."""
        error_result = MagicMock(spec=ExecutionResult)
        error_result.termination_reason = TerminationReason.ERROR
        mock_build_result.return_value = error_result

        ctx = _make_context()
        escalation = _make_escalation()
        invoker = _make_tool_invoker(escalations=(escalation,))
        response = _make_response_with_tool_calls()

        gate = MagicMock(spec=ApprovalGate)
        gate.should_park.return_value = escalation
        gate.park_context = AsyncMock(
            side_effect=OSError("disk full"),
        )

        result = await execute_tool_calls(
            ctx,
            invoker,
            response,
            1,
            [],
            approval_gate=gate,
        )
        assert result is error_result
        call_kwargs = mock_build_result.call_args
        assert call_kwargs[0][1] == TerminationReason.ERROR
