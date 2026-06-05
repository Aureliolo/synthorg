"""Unit tests for ToolInvoker sub-constraint enforcement."""

from typing import Any, override

import pytest

from synthorg.core.enums import ActionType, ToolAccessLevel, ToolCategory
from synthorg.core.tool_constraints import (
    NetworkMode,
    TerminalAccess,
    ToolSubConstraints,
)
from synthorg.providers.models import ToolCall
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.permissions import ToolPermissionChecker
from synthorg.tools.registry import ToolRegistry

# ── Test tools ─────────────────────────────────────────────────


class _WebTool(BaseTool):
    """Test web tool."""

    def __init__(self) -> None:
        super().__init__(
            name="http_request",
            description="Make HTTP requests",
            category=ToolCategory.WEB,
            parameters_schema={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
                "additionalProperties": False,
            },
        )

    @override
    async def execute(self, *, arguments: dict[str, Any]) -> ToolExecutionResult:
        return ToolExecutionResult(content="ok")


class _TerminalTool(BaseTool):
    """Test terminal tool."""

    def __init__(self) -> None:
        super().__init__(
            name="shell_command",
            description="Run shell commands",
            category=ToolCategory.TERMINAL,
            parameters_schema={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
        )

    @override
    async def execute(self, *, arguments: dict[str, Any]) -> ToolExecutionResult:
        return ToolExecutionResult(content="ok")


class _FileTool(BaseTool):
    """Test file system tool."""

    def __init__(self) -> None:
        super().__init__(
            name="read_file",
            description="Read files",
            category=ToolCategory.FILE_SYSTEM,
            action_type=ActionType.CODE_READ,
            parameters_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    @override
    async def execute(self, *, arguments: dict[str, Any]) -> ToolExecutionResult:
        return ToolExecutionResult(content="file content")


# ── Fixtures ───────────────────────────────────────────────────


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry([_WebTool(), _TerminalTool(), _FileTool()])


# ── Tests ──────────────────────────────────────────────────────


class TestInvokerSubConstraints:
    """Tests for sub-constraint enforcement in the ToolInvoker pipeline."""

    @pytest.mark.unit
    async def test_network_none_blocks_web_tool(self, registry: ToolRegistry) -> None:
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.ELEVATED,
            sub_constraints=ToolSubConstraints(network=NetworkMode.NONE),
        )
        invoker = ToolInvoker(registry, permission_checker=checker)
        call = ToolCall(id="c1", name="http_request", arguments={"url": "http://x.com"})
        result = await invoker.invoke(call)
        assert result.is_error is True
        assert "sub-constraint" in result.content.lower()

    @pytest.mark.unit
    async def test_terminal_none_blocks_terminal_tool(
        self, registry: ToolRegistry
    ) -> None:
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.ELEVATED,
            sub_constraints=ToolSubConstraints(terminal=TerminalAccess.NONE),
        )
        invoker = ToolInvoker(registry, permission_checker=checker)
        call = ToolCall(id="c2", name="shell_command", arguments={"command": "ls"})
        result = await invoker.invoke(call)
        assert result.is_error is True
        assert "terminal" in result.content.lower()

    @pytest.mark.unit
    async def test_open_network_allows_web_tool(self, registry: ToolRegistry) -> None:
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.ELEVATED,
            sub_constraints=ToolSubConstraints(network=NetworkMode.OPEN),
        )
        invoker = ToolInvoker(registry, permission_checker=checker)
        call = ToolCall(id="c3", name="http_request", arguments={"url": "http://x.com"})
        result = await invoker.invoke(call)
        assert result.is_error is False
        assert result.content == "ok"

    @pytest.mark.unit
    async def test_file_tool_passes_all_sub_constraints(
        self, registry: ToolRegistry
    ) -> None:
        """File tools are unaffected by network/terminal sub-constraints."""
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.SANDBOXED,
        )
        invoker = ToolInvoker(registry, permission_checker=checker)
        call = ToolCall(id="c4", name="read_file", arguments={"path": "test.txt"})
        result = await invoker.invoke(call)
        assert result.is_error is False

    @pytest.mark.unit
    async def test_no_permission_checker_skips_sub_constraints(
        self, registry: ToolRegistry
    ) -> None:
        """When no permission checker is set, sub-constraints are skipped."""
        invoker = ToolInvoker(registry)
        call = ToolCall(id="c5", name="http_request", arguments={"url": "http://x.com"})
        result = await invoker.invoke(call)
        assert result.is_error is False

    @pytest.mark.unit
    async def test_requires_approval_returns_escalation(
        self, registry: ToolRegistry
    ) -> None:
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.ELEVATED,
            sub_constraints=ToolSubConstraints(
                requires_approval=("comms",),
            ),
        )
        invoker = ToolInvoker(registry, permission_checker=checker)
        call = ToolCall(id="c6", name="http_request", arguments={"url": "http://x.com"})
        result = await invoker.invoke(call)
        assert result.is_error is True
        assert "approval" in result.content.lower()
