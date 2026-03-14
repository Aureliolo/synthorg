"""Tests for approval gate integration in loop helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_company.core.enums import ApprovalRiskLevel
from ai_company.engine.approval_gate import ApprovalGate
from ai_company.engine.approval_gate_models import EscalationInfo
from ai_company.engine.loop_helpers import execute_tool_calls
from ai_company.engine.loop_protocol import ExecutionResult, TerminationReason
from ai_company.providers.enums import FinishReason
from ai_company.providers.models import (
    ZERO_TOKEN_USAGE,
    CompletionResponse,
    ToolCall,
    ToolResult,
)

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def _make_escalation(
    approval_id: str = "approval-1",
) -> EscalationInfo:
    return EscalationInfo(
        approval_id=approval_id,
        tool_call_id="tc-1",
        tool_name="deploy_to_prod",
        action_type="deploy:production",
        risk_level=ApprovalRiskLevel.HIGH,
        reason="Needs approval",
    )


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

    @patch("ai_company.engine.loop_helpers.build_result")
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

    @patch("ai_company.engine.loop_helpers.build_result")
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

    @patch("ai_company.engine.loop_helpers.build_result")
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

    @patch("ai_company.engine.loop_helpers.build_result")
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
        ctx.task_execution = None  # No task — taskless agent
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

    @patch("ai_company.engine.loop_helpers.build_result")
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
