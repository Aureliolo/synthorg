"""Tests for ToolInvoker security interception integration."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ai_company.core.enums import ApprovalRiskLevel, ToolCategory
from ai_company.providers.models import ToolCall
from ai_company.security.models import (
    OutputScanResult,
    SecurityContext,
    SecurityVerdict,
    SecurityVerdictType,
)
from ai_company.tools.base import BaseTool, ToolExecutionResult
from ai_company.tools.invoker import ToolInvoker
from ai_company.tools.registry import ToolRegistry

pytestmark = pytest.mark.timeout(30)


# ── Concrete test tool ───────────────────────────────────────────


class _SecurityTestTool(BaseTool):
    """Simple tool for security integration tests."""

    def __init__(
        self,
        *,
        name: str = "secure_tool",
        category: ToolCategory = ToolCategory.FILE_SYSTEM,
        action_type: str | None = None,
    ) -> None:
        super().__init__(
            name=name,
            description=f"Test tool: {name}",
            category=category,
            action_type=action_type,
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            content=f"executed: {arguments.get('cmd', 'default')}",
        )


# ── Helpers ──────────────────────────────────────────────────────

_NOW = datetime.now(UTC)


def _make_verdict(
    *,
    verdict: SecurityVerdictType = SecurityVerdictType.ALLOW,
    reason: str = "test reason",
    risk_level: ApprovalRiskLevel = ApprovalRiskLevel.LOW,
    approval_id: str | None = None,
) -> SecurityVerdict:
    """Build a SecurityVerdict with sensible defaults."""
    return SecurityVerdict(
        verdict=verdict,
        reason=reason,
        risk_level=risk_level,
        evaluated_at=_NOW,
        evaluation_duration_ms=1.0,
        approval_id=approval_id,
    )


def _make_interceptor(
    *,
    pre_tool_verdict: SecurityVerdict | None = None,
    scan_result: OutputScanResult | None = None,
) -> AsyncMock:
    """Build a mock SecurityInterceptionStrategy."""
    interceptor = AsyncMock()
    interceptor.evaluate_pre_tool = AsyncMock(
        return_value=pre_tool_verdict or _make_verdict(),
    )
    interceptor.scan_output = AsyncMock(
        return_value=scan_result or OutputScanResult(),
    )
    return interceptor


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def secure_tool() -> _SecurityTestTool:
    return _SecurityTestTool()


@pytest.fixture
def security_registry(secure_tool: _SecurityTestTool) -> ToolRegistry:
    return ToolRegistry([secure_tool])


@pytest.fixture
def tool_call() -> ToolCall:
    return ToolCall(
        id="call_sec_001",
        name="secure_tool",
        arguments={"cmd": "ls"},
    )


# ── No interceptor → normal execution ───────────────────────────


@pytest.mark.unit
class TestNoInterceptor:
    """When no security interceptor is configured, tools execute normally."""

    async def test_invoke_without_interceptor(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        """Tool executes successfully with no security checks."""
        invoker = ToolInvoker(security_registry)
        result = await invoker.invoke(tool_call)
        assert result.is_error is False
        assert result.content == "executed: ls"
        assert result.tool_call_id == tool_call.id

    async def test_output_not_scanned_without_interceptor(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        """Output passes through unmodified without an interceptor."""
        invoker = ToolInvoker(security_registry)
        result = await invoker.invoke(tool_call)
        assert "executed: ls" in result.content


# ── ALLOW verdict → tool executes normally ───────────────────────


@pytest.mark.unit
class TestAllowVerdict:
    """When interceptor returns ALLOW, tool executes normally."""

    async def test_allow_verdict_lets_tool_run(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
            agent_id="agent-1",
            task_id="task-1",
        )
        result = await invoker.invoke(tool_call)
        assert result.is_error is False
        assert result.content == "executed: ls"

    async def test_allow_verdict_calls_evaluate_pre_tool(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
            agent_id="agent-1",
            task_id="task-1",
        )
        await invoker.invoke(tool_call)
        interceptor.evaluate_pre_tool.assert_awaited_once()


# ── DENY verdict → ToolResult(is_error=True) ────────────────────


@pytest.mark.unit
class TestDenyVerdict:
    """When interceptor returns DENY, tool does not execute."""

    async def test_deny_returns_error_result(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(
                verdict=SecurityVerdictType.DENY,
                reason="dangerous operation",
            ),
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        result = await invoker.invoke(tool_call)
        assert result.is_error is True
        assert "Security denied" in result.content
        assert "dangerous operation" in result.content
        assert result.tool_call_id == tool_call.id

    async def test_deny_does_not_execute_tool(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        """Tool's execute method is never called on DENY."""
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.DENY),
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        result = await invoker.invoke(tool_call)
        assert result.is_error is True
        # If execute had run, content would contain "executed:"
        assert "executed:" not in result.content

    async def test_deny_skips_output_scan(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        """scan_output is not called when pre-tool check denies."""
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.DENY),
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        await invoker.invoke(tool_call)
        interceptor.scan_output.assert_not_awaited()


# ── ESCALATE verdict → ToolResult with approval_id ───────────────


@pytest.mark.unit
class TestEscalateVerdict:
    """When interceptor returns ESCALATE, tool does not execute."""

    async def test_escalate_returns_error_with_approval_id(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(
                verdict=SecurityVerdictType.ESCALATE,
                reason="requires manager approval",
                approval_id="approval-42",
            ),
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        result = await invoker.invoke(tool_call)
        assert result.is_error is True
        assert "Security escalation" in result.content
        assert "requires manager approval" in result.content
        assert "approval-42" in result.content
        assert result.tool_call_id == tool_call.id

    async def test_escalate_does_not_execute_tool(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(
                verdict=SecurityVerdictType.ESCALATE,
                approval_id="approval-99",
            ),
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        result = await invoker.invoke(tool_call)
        assert "executed:" not in result.content

    async def test_escalate_skips_output_scan(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(
                verdict=SecurityVerdictType.ESCALATE,
                approval_id="approval-77",
            ),
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        await invoker.invoke(tool_call)
        interceptor.scan_output.assert_not_awaited()


# ── Output scanning: sensitive data → redacted ───────────────────


@pytest.mark.unit
class TestOutputScanRedaction:
    """Tests for output scanning and redaction."""

    async def test_sensitive_output_is_redacted(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
            scan_result=OutputScanResult(
                has_sensitive_data=True,
                findings=("API key detected",),
                redacted_content="executed: [REDACTED]",
            ),
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        result = await invoker.invoke(tool_call)
        assert result.is_error is False
        assert result.content == "executed: [REDACTED]"

    async def test_clean_output_passes_through(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
            scan_result=OutputScanResult(
                has_sensitive_data=False,
            ),
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        result = await invoker.invoke(tool_call)
        assert result.is_error is False
        assert result.content == "executed: ls"

    async def test_scan_output_called_after_successful_execution(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        await invoker.invoke(tool_call)
        interceptor.scan_output.assert_awaited_once()

    async def test_sensitive_but_no_redacted_content_fails_closed(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        """If has_sensitive_data=True but redacted_content is None, fail-closed."""
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
            scan_result=OutputScanResult(
                has_sensitive_data=True,
                findings=("potential leak",),
                redacted_content=None,
            ),
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        result = await invoker.invoke(tool_call)
        assert result.is_error is True
        assert "fail-closed" in result.content.lower()
        assert "executed:" not in result.content


# ── SecurityContext construction ─────────────────────────────────


@pytest.mark.unit
class TestSecurityContextConstruction:
    """Tests that SecurityContext is built correctly from tool + tool_call."""

    async def test_context_has_correct_tool_name(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        interceptor = _make_interceptor()
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
            agent_id="agent-a",
            task_id="task-b",
        )
        await invoker.invoke(tool_call)

        context: SecurityContext = interceptor.evaluate_pre_tool.call_args[0][0]
        assert context.tool_name == "secure_tool"

    async def test_context_has_correct_category(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        interceptor = _make_interceptor()
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        await invoker.invoke(tool_call)

        context: SecurityContext = interceptor.evaluate_pre_tool.call_args[0][0]
        assert context.tool_category == ToolCategory.FILE_SYSTEM

    async def test_context_has_correct_action_type(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        interceptor = _make_interceptor()
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        await invoker.invoke(tool_call)

        context: SecurityContext = interceptor.evaluate_pre_tool.call_args[0][0]
        # FILE_SYSTEM default action_type is code:write
        assert context.action_type == "code:write"

    async def test_context_has_correct_arguments(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        interceptor = _make_interceptor()
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        await invoker.invoke(tool_call)

        context: SecurityContext = interceptor.evaluate_pre_tool.call_args[0][0]
        assert context.arguments == {"cmd": "ls"}

    async def test_context_carries_agent_and_task_ids(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        interceptor = _make_interceptor()
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
            agent_id="agent-x",
            task_id="task-y",
        )
        await invoker.invoke(tool_call)

        context: SecurityContext = interceptor.evaluate_pre_tool.call_args[0][0]
        assert context.agent_id == "agent-x"
        assert context.task_id == "task-y"

    async def test_context_with_none_ids(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        """agent_id and task_id default to None when not provided."""
        interceptor = _make_interceptor()
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        await invoker.invoke(tool_call)

        context: SecurityContext = interceptor.evaluate_pre_tool.call_args[0][0]
        assert context.agent_id is None
        assert context.task_id is None

    async def test_context_with_custom_action_type(
        self,
        tool_call: ToolCall,
    ) -> None:
        """Custom action_type on tool propagates to SecurityContext."""
        custom_tool = _SecurityTestTool(action_type="deploy:production")
        registry = ToolRegistry([custom_tool])
        interceptor = _make_interceptor()
        invoker = ToolInvoker(
            registry,
            security_interceptor=interceptor,
        )
        await invoker.invoke(tool_call)

        context: SecurityContext = interceptor.evaluate_pre_tool.call_args[0][0]
        assert context.action_type == "deploy:production"

    async def test_scan_exception_returns_error_result(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        """When scan_output raises, fail-closed returns an error result."""
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
        )
        interceptor.scan_output = AsyncMock(
            side_effect=RuntimeError("scan crashed"),
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        result = await invoker.invoke(tool_call)
        assert result.is_error is True
        assert "fail-closed" in result.content.lower()
        # Original tool output should NOT be returned.
        assert "executed:" not in result.content

    async def test_scan_output_context_matches_pre_tool_context(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        """Both evaluate_pre_tool and scan_output receive equivalent contexts."""
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
            agent_id="agent-z",
            task_id="task-z",
        )
        await invoker.invoke(tool_call)

        pre_ctx: SecurityContext = interceptor.evaluate_pre_tool.call_args[0][0]
        scan_ctx: SecurityContext = interceptor.scan_output.call_args[0][0]
        assert pre_ctx.tool_name == scan_ctx.tool_name
        assert pre_ctx.tool_category == scan_ctx.tool_category
        assert pre_ctx.action_type == scan_ctx.action_type
        assert pre_ctx.agent_id == scan_ctx.agent_id
        assert pre_ctx.task_id == scan_ctx.task_id
