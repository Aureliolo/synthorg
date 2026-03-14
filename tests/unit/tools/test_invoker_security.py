"""Tests for ToolInvoker security interception integration."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import ApprovalRiskLevel, ToolCategory
from synthorg.providers.models import ToolCall
from synthorg.security.models import (
    OutputScanResult,
    ScanOutcome,
    SecurityContext,
    SecurityVerdict,
    SecurityVerdictType,
)
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry

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


class _FailingSecurityTool(_SecurityTestTool):
    """Tool that raises RuntimeError from execute."""

    def __init__(self) -> None:
        super().__init__(name="failing_tool")

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        msg = "intentional failure"
        raise RuntimeError(msg)


class _SoftErrorSecurityTool(_SecurityTestTool):
    """Tool that returns is_error=True with sensitive content."""

    def __init__(self) -> None:
        super().__init__(name="soft_error_tool")

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            is_error=True,
            content="error: API_KEY=AKIA1234567890EXAMPLE",
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
                outcome=ScanOutcome.REDACTED,
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

    async def test_withheld_outcome_returns_policy_message(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        """WITHHELD outcome returns explicit policy message (not fail-closed)."""
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
            scan_result=OutputScanResult(
                has_sensitive_data=True,
                findings=("potential leak",),
                redacted_content=None,
                outcome=ScanOutcome.WITHHELD,
            ),
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        result = await invoker.invoke(tool_call)
        assert result.is_error is True
        assert "withheld by security policy" in result.content.lower()
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


# ── Gap 1: Non-recoverable errors from scan propagate ────────────


@pytest.mark.unit
class TestOutputScanNonRecoverableErrors:
    """MemoryError/RecursionError from scan_output propagate."""

    @pytest.mark.parametrize(
        ("exc", "exc_cls"),
        [
            (MemoryError("oom"), MemoryError),
            (RecursionError("max depth"), RecursionError),
        ],
    )
    async def test_non_recoverable_scan_errors_propagate(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
        exc: BaseException,
        exc_cls: type[BaseException],
    ) -> None:
        """Non-recoverable errors from scan_output propagate."""
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
        )
        interceptor.scan_output = AsyncMock(side_effect=exc)
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        with pytest.raises(exc_cls):
            await invoker.invoke(tool_call)


# ── Gap 2: Tool execution error skips output scan ────────────────


@pytest.mark.unit
class TestOutputScanSkippedOnToolError:
    """When tool.execute() raises, scan_output is not called."""

    async def test_tool_execution_error_skips_output_scan(self) -> None:
        registry = ToolRegistry([_FailingSecurityTool()])
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
        )
        invoker = ToolInvoker(
            registry,
            security_interceptor=interceptor,
        )
        call = ToolCall(id="call_fail", name="failing_tool", arguments={})

        result = await invoker.invoke(call)

        assert result.is_error is True
        interceptor.scan_output.assert_not_awaited()


# ── Gap 3: scan_output receives tool result content ──────────────


@pytest.mark.unit
class TestOutputScanContentPassing:
    """Verify scan_output receives the tool's actual output string."""

    async def test_scan_output_receives_tool_result_content(
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
        )
        await invoker.invoke(tool_call)

        # Second positional arg to scan_output is the output string.
        scan_call_args = interceptor.scan_output.call_args[0]
        assert scan_call_args[1] == "executed: ls"


# ── Gap 4: invoke_all output scanning ────────────────────────────


@pytest.mark.unit
class TestInvokeAllOutputScanning:
    """Output scanning in invoke_all with multiple tool calls."""

    async def test_invoke_all_scans_each_tool_output(
        self,
        security_registry: ToolRegistry,
    ) -> None:
        """scan_output is called once per tool call."""
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        calls = [
            ToolCall(id="call_a", name="secure_tool", arguments={"cmd": "a"}),
            ToolCall(id="call_b", name="secure_tool", arguments={"cmd": "b"}),
        ]

        results = await invoker.invoke_all(calls)

        assert len(results) == 2
        assert interceptor.scan_output.await_count == 2

    async def test_invoke_all_with_redaction(
        self,
        security_registry: ToolRegistry,
    ) -> None:
        """Redaction applies to each tool result in invoke_all."""
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
            scan_result=OutputScanResult(
                has_sensitive_data=True,
                findings=("secret",),
                redacted_content="[REDACTED]",
                outcome=ScanOutcome.REDACTED,
            ),
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        calls = [
            ToolCall(id="call_1", name="secure_tool", arguments={"cmd": "x"}),
            ToolCall(id="call_2", name="secure_tool", arguments={"cmd": "y"}),
        ]

        results = await invoker.invoke_all(calls)

        assert all(r.content == "[REDACTED]" for r in results)
        assert all(r.is_error is False for r in results)


# ── Gap 5: Soft error content is scanned ─────────────────────────


@pytest.mark.unit
class TestOutputScanOnSoftError:
    """Tool returning is_error=True still gets output scanned."""

    async def test_soft_error_content_is_scanned(self) -> None:
        """When tool returns is_error=True, scan_output is still called."""
        registry = ToolRegistry([_SoftErrorSecurityTool()])
        scan_result = OutputScanResult(
            has_sensitive_data=True,
            findings=("API key",),
            redacted_content="error: [REDACTED]",
            outcome=ScanOutcome.REDACTED,
        )
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
            scan_result=scan_result,
        )
        invoker = ToolInvoker(
            registry,
            security_interceptor=interceptor,
        )
        call = ToolCall(id="call_soft", name="soft_error_tool", arguments={})

        result = await invoker.invoke(call)

        interceptor.scan_output.assert_awaited_once()
        assert result.is_error is True
        assert result.content == "error: [REDACTED]"

    async def test_soft_error_scan_receives_error_content(self) -> None:
        """Verify scan_output receives the error content string."""
        registry = ToolRegistry([_SoftErrorSecurityTool()])
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
        )
        invoker = ToolInvoker(
            registry,
            security_interceptor=interceptor,
        )
        call = ToolCall(id="call_soft2", name="soft_error_tool", arguments={})

        await invoker.invoke(call)

        scan_args = interceptor.scan_output.call_args[0]
        assert scan_args[1] == "error: API_KEY=AKIA1234567890EXAMPLE"
