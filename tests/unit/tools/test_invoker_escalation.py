"""Tests for ToolInvoker escalation tracking."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import ApprovalRiskLevel, ToolCategory
from synthorg.providers.models import ToolCall
from synthorg.security.models import SecurityVerdict, SecurityVerdictType
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

_NOW = datetime.now(UTC)


def _verdict(
    verdict_type: SecurityVerdictType,
    *,
    reason: str = "test reason",
    risk_level: ApprovalRiskLevel = ApprovalRiskLevel.HIGH,
    approval_id: str | None = None,
) -> SecurityVerdict:
    """Helper to build a SecurityVerdict with required fields."""
    return SecurityVerdict(
        verdict=verdict_type,
        reason=reason,
        risk_level=risk_level,
        approval_id=approval_id,
        evaluated_at=_NOW,
        evaluation_duration_ms=0.0,
    )


class _StubTool(BaseTool):
    """Stub tool for testing."""

    def __init__(self, name: str = "stub_tool") -> None:
        super().__init__(
            name=name,
            description="A test stub",
            category=ToolCategory.OTHER,
            action_type="comms:internal",
        )

    async def execute(self, *, arguments: dict[str, Any]) -> ToolExecutionResult:
        return ToolExecutionResult(content="ok")


class _ParkingTool(BaseTool):
    """Tool that returns requires_parking metadata."""

    def __init__(self, approval_id: str = "approval-parking-1") -> None:
        super().__init__(
            name="parking_tool",
            description="A tool that parks",
            category=ToolCategory.OTHER,
            action_type="comms:internal",
        )
        self._approval_id = approval_id

    async def execute(self, *, arguments: dict[str, Any]) -> ToolExecutionResult:
        return ToolExecutionResult(
            content="Parking required",
            metadata={
                "requires_parking": True,
                "approval_id": self._approval_id,
            },
        )


def _make_invoker(
    *tools: BaseTool,
    security_interceptor: object | None = None,
) -> ToolInvoker:
    registry = ToolRegistry(tools)
    return ToolInvoker(
        registry,
        security_interceptor=security_interceptor,  # type: ignore[arg-type]
        agent_id="agent-1",
        task_id="task-1",
    )


def _make_tool_call(
    name: str = "stub_tool",
    call_id: str = "tc-1",
) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments={})


class TestPendingEscalationsEmpty:
    """pending_escalations is empty when no escalations occur."""

    def test_empty_initially(self) -> None:
        invoker = _make_invoker(_StubTool())
        assert invoker.pending_escalations == ()

    async def test_empty_after_normal_invoke(self) -> None:
        invoker = _make_invoker(_StubTool())
        await invoker.invoke(_make_tool_call())
        assert invoker.pending_escalations == ()

    async def test_empty_after_normal_invoke_all(self) -> None:
        invoker = _make_invoker(_StubTool())
        await invoker.invoke_all([_make_tool_call()])
        assert invoker.pending_escalations == ()


class TestEscalateVerdict:
    """Escalation tracked on ESCALATE verdict with approval_id."""

    async def test_populated_on_escalate_with_approval_id(self) -> None:
        interceptor = AsyncMock()
        interceptor.evaluate_pre_tool = AsyncMock(
            return_value=_verdict(
                SecurityVerdictType.ESCALATE,
                reason="Needs approval",
                approval_id="approval-sec-1",
            ),
        )
        invoker = _make_invoker(_StubTool(), security_interceptor=interceptor)
        await invoker.invoke(_make_tool_call())

        escalations = invoker.pending_escalations
        assert len(escalations) == 1
        assert escalations[0].approval_id == "approval-sec-1"
        assert escalations[0].tool_call_id == "tc-1"
        assert escalations[0].tool_name == "stub_tool"
        assert escalations[0].risk_level == ApprovalRiskLevel.HIGH

    async def test_not_populated_on_escalate_without_approval_id(self) -> None:
        interceptor = AsyncMock()
        interceptor.evaluate_pre_tool = AsyncMock(
            return_value=_verdict(
                SecurityVerdictType.ESCALATE,
                reason="Needs approval",
            ),
        )
        invoker = _make_invoker(_StubTool(), security_interceptor=interceptor)
        await invoker.invoke(_make_tool_call())
        assert invoker.pending_escalations == ()

    async def test_not_populated_on_allow_verdict(self) -> None:
        interceptor = AsyncMock()
        interceptor.evaluate_pre_tool = AsyncMock(
            return_value=_verdict(
                SecurityVerdictType.ALLOW,
                reason="OK",
                risk_level=ApprovalRiskLevel.LOW,
            ),
        )
        interceptor.scan_output = AsyncMock(
            return_value=AsyncMock(has_sensitive_data=False),
        )
        invoker = _make_invoker(_StubTool(), security_interceptor=interceptor)
        await invoker.invoke(_make_tool_call())
        assert invoker.pending_escalations == ()

    async def test_not_populated_on_deny_verdict(self) -> None:
        interceptor = AsyncMock()
        interceptor.evaluate_pre_tool = AsyncMock(
            return_value=_verdict(
                SecurityVerdictType.DENY,
                reason="Blocked",
                risk_level=ApprovalRiskLevel.CRITICAL,
            ),
        )
        invoker = _make_invoker(_StubTool(), security_interceptor=interceptor)
        await invoker.invoke(_make_tool_call())
        assert invoker.pending_escalations == ()


class TestClearBetweenCalls:
    """Escalations are cleared between calls."""

    async def test_cleared_between_invoke_calls(self) -> None:
        call_count = 0

        async def _escalate_first(
            *_args: object,
            **_kwargs: object,
        ) -> SecurityVerdict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _verdict(
                    SecurityVerdictType.ESCALATE,
                    reason="Needs approval",
                    approval_id="approval-1",
                )
            return _verdict(SecurityVerdictType.DENY, reason="Denied")

        interceptor = AsyncMock()
        interceptor.evaluate_pre_tool = AsyncMock(side_effect=_escalate_first)
        invoker = _make_invoker(_StubTool(), security_interceptor=interceptor)

        await invoker.invoke(_make_tool_call())
        assert len(invoker.pending_escalations) == 1

        await invoker.invoke(_make_tool_call(call_id="tc-2"))
        assert len(invoker.pending_escalations) == 0

    async def test_cleared_at_start_of_invoke_all(self) -> None:
        call_count = 0

        async def _escalate_first(
            *_args: object,
            **_kwargs: object,
        ) -> SecurityVerdict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _verdict(
                    SecurityVerdictType.ESCALATE,
                    reason="Needs approval",
                    approval_id="approval-1",
                )
            return _verdict(SecurityVerdictType.DENY, reason="Denied")

        interceptor = AsyncMock()
        interceptor.evaluate_pre_tool = AsyncMock(side_effect=_escalate_first)
        invoker = _make_invoker(_StubTool(), security_interceptor=interceptor)

        await invoker.invoke(_make_tool_call())
        assert len(invoker.pending_escalations) == 1

        await invoker.invoke_all([_make_tool_call(call_id="tc-2")])
        assert len(invoker.pending_escalations) == 0


class TestMultipleEscalationsInvokeAll:
    """Multiple escalations tracked in invoke_all."""

    async def test_multiple_escalations(self) -> None:
        interceptor = AsyncMock()
        interceptor.evaluate_pre_tool = AsyncMock(
            return_value=_verdict(
                SecurityVerdictType.ESCALATE,
                reason="Needs approval",
                approval_id="approval-multi",
            ),
        )
        tool_a = _StubTool("tool_a")
        tool_b = _StubTool("tool_b")
        invoker = _make_invoker(tool_a, tool_b, security_interceptor=interceptor)

        await invoker.invoke_all(
            [
                _make_tool_call("tool_a", "tc-a"),
                _make_tool_call("tool_b", "tc-b"),
            ]
        )

        escalations = invoker.pending_escalations
        assert len(escalations) == 2


class TestParkingToolMetadata:
    """Escalation from tool metadata (requires_parking)."""

    async def test_parking_metadata_creates_escalation(self) -> None:
        invoker = _make_invoker(_ParkingTool())
        await invoker.invoke(_make_tool_call("parking_tool"))

        escalations = invoker.pending_escalations
        assert len(escalations) == 1
        assert escalations[0].approval_id == "approval-parking-1"
        assert escalations[0].tool_name == "parking_tool"
        assert escalations[0].reason == "Agent requested human approval"
        assert escalations[0].risk_level == ApprovalRiskLevel.HIGH

    async def test_no_escalation_without_parking_metadata(self) -> None:
        invoker = _make_invoker(_StubTool())
        await invoker.invoke(_make_tool_call())
        assert invoker.pending_escalations == ()
