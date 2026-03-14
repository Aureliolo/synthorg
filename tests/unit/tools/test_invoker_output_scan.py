"""Tests for ScanOutcome-driven output scan handling in ToolInvoker.

Covers the WITHHELD, LOG_ONLY, and defensive fallback branches of
``handle_sensitive_scan`` (exercised via the ``ToolInvoker`` flow).
Split from ``test_invoker_security.py`` to keep file sizes under
800 lines.
"""

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

_NOW = datetime.now(UTC)


# ── Concrete test tool ───────────────────────────────────────────


class _OutputScanTestTool(BaseTool):
    """Simple tool for output scan integration tests."""

    def __init__(self) -> None:
        super().__init__(
            name="secure_tool",
            description="Test tool: secure_tool",
            category=ToolCategory.FILE_SYSTEM,
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
def security_registry() -> ToolRegistry:
    return ToolRegistry([_OutputScanTestTool()])


@pytest.fixture
def tool_call() -> ToolCall:
    return ToolCall(
        id="call_scan_001",
        name="secure_tool",
        arguments={"cmd": "ls"},
    )


# ── Withheld outcome tests ─────────────────────────────────────


@pytest.mark.unit
class TestWithheldOutcome:
    """Tests for the WITHHELD scan outcome path in the invoker."""

    async def test_withheld_outcome_returns_policy_message(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        """Withheld outcome returns explicit policy message."""
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
            scan_result=OutputScanResult(
                has_sensitive_data=True,
                findings=("secret token",),
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

    async def test_withheld_metadata_uses_output_withheld_key(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        """Withheld outcome sets output_withheld metadata, not output_scan_failed."""
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
            scan_result=OutputScanResult(
                has_sensitive_data=True,
                findings=("credential",),
                redacted_content=None,
                outcome=ScanOutcome.WITHHELD,
            ),
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        # Access _scan_output to inspect ToolExecutionResult.metadata
        # (ToolResult does not surface the metadata field from the
        # execution layer, so the public invoke() API cannot verify
        # metadata keys).
        tool_exec_result = ToolExecutionResult(
            content="raw output",
            metadata={"sentinel": True},
        )
        context = SecurityContext(
            tool_name="secure_tool",
            tool_category=ToolCategory.FILE_SYSTEM,
            action_type="code:write",
        )
        scan_exec = await invoker._scan_output(tool_call, tool_exec_result, context)
        assert scan_exec.metadata.get("output_withheld") is True
        assert scan_exec.metadata.get("sentinel") is True
        assert "output_scan_failed" not in scan_exec.metadata

    async def test_withheld_takes_priority_over_redacted_content(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        """WITHHELD outcome withholds even when redacted_content is present.

        The model validator now rejects WITHHELD + non-None redacted_content
        at construction time.  This test uses ``model_copy`` (which skips
        validators) to verify the invoker's defence-in-depth branching:
        WITHHELD is checked before redacted_content.
        """
        # Build a valid WITHHELD result, then sneak in redacted_content
        # via model_copy (which bypasses the model validator).
        base = OutputScanResult(
            has_sensitive_data=True,
            findings=("token",),
            redacted_content=None,
            outcome=ScanOutcome.WITHHELD,
        )
        broken = base.model_copy(
            update={"redacted_content": "partially redacted output"},
        )
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
            scan_result=broken,
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        result = await invoker.invoke(tool_call)
        assert result.is_error is True
        assert "withheld by security policy" in result.content.lower()
        assert "partially redacted" not in result.content


# ── LOG_ONLY outcome tests ──────────────────────────────────────


@pytest.mark.unit
class TestLogOnlyOutcome:
    """Tests for the LOG_ONLY scan outcome path in the invoker."""

    async def test_log_only_passes_original_output_through(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        """LOG_ONLY outcome passes original tool output through unchanged."""
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
            scan_result=OutputScanResult(outcome=ScanOutcome.LOG_ONLY),
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        result = await invoker.invoke(tool_call)
        assert result.is_error is False
        assert result.content == "executed: ls"


# ── Defensive fallback tests ────────────────────────────────────


@pytest.mark.unit
class TestDefensiveFallback:
    """Tests for the defensive fail-closed fallback in handle_sensitive_scan.

    Exercised via ``ToolInvoker._scan_output``.  This branch catches
    unexpected states where ``has_sensitive_data=True`` but outcome is
    not ``WITHHELD`` and ``redacted_content`` is ``None``.  Reachable
    when ``model_copy()`` skips validators.
    """

    async def test_defensive_fallback_withholds_on_unexpected_state(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        """Defensive fallback withholds when scan has sensitive data
        but neither WITHHELD outcome nor redacted_content."""
        # Simulate a broken policy via model_copy (skips validators):
        # REDACTED outcome but redacted_content cleared.
        base = OutputScanResult(
            has_sensitive_data=True,
            findings=("leak",),
            redacted_content="safe",
            outcome=ScanOutcome.REDACTED,
        )
        broken = base.model_copy(update={"redacted_content": None})
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
            scan_result=broken,
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        result = await invoker.invoke(tool_call)
        assert result.is_error is True
        assert "fail-closed" in result.content.lower()
        assert "executed:" not in result.content

    async def test_defensive_fallback_metadata_uses_scan_failed_key(
        self,
        security_registry: ToolRegistry,
        tool_call: ToolCall,
    ) -> None:
        """Defensive fallback sets output_scan_failed metadata."""
        base = OutputScanResult(
            has_sensitive_data=True,
            findings=("leak",),
            redacted_content="safe",
            outcome=ScanOutcome.REDACTED,
        )
        broken = base.model_copy(update={"redacted_content": None})
        interceptor = _make_interceptor(
            pre_tool_verdict=_make_verdict(verdict=SecurityVerdictType.ALLOW),
            scan_result=broken,
        )
        invoker = ToolInvoker(
            security_registry,
            security_interceptor=interceptor,
        )
        tool_exec_result = ToolExecutionResult(
            content="raw output",
            metadata={"sentinel": True},
        )
        context = SecurityContext(
            tool_name="secure_tool",
            tool_category=ToolCategory.FILE_SYSTEM,
            action_type="code:write",
        )
        scan_exec = await invoker._scan_output(tool_call, tool_exec_result, context)
        assert scan_exec.metadata.get("output_scan_failed") is True
        assert scan_exec.metadata.get("sentinel") is True
        assert "output_withheld" not in scan_exec.metadata
