"""Unit test fixtures for the tool system."""

from typing import Any

import pytest

from ai_company.providers.models import ToolCall
from ai_company.tools.base import BaseTool, ToolExecutionResult
from ai_company.tools.invoker import ToolInvoker
from ai_company.tools.registry import ToolRegistry

# ── Concrete test tools (private to tests) ────────────────────────


class _EchoTestTool(BaseTool):
    """Returns arguments as content."""

    def __init__(self) -> None:
        super().__init__(
            name="echo_test",
            description="Echoes arguments back",
            parameters_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return ToolExecutionResult(content=arguments.get("message", ""))


class _FailingTool(BaseTool):
    """Always raises RuntimeError in execute."""

    def __init__(self) -> None:
        super().__init__(
            name="failing",
            description="Always fails",
            parameters_schema={
                "type": "object",
                "properties": {"input": {"type": "string"}},
            },
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        msg = "tool execution failed"
        raise RuntimeError(msg)


class _NoSchemaTool(BaseTool):
    """Tool with no parameters schema."""

    def __init__(self) -> None:
        super().__init__(
            name="no_schema",
            description="Accepts anything",
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return ToolExecutionResult(content="ok")


class _StrictSchemaTool(BaseTool):
    """Tool with strict schema: requires query + limit, no extras."""

    def __init__(self) -> None:
        super().__init__(
            name="strict",
            description="Strict parameters",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query", "limit"],
                "additionalProperties": False,
            },
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            content=f"query={arguments['query']} limit={arguments['limit']}",
        )


class _SoftErrorTool(BaseTool):
    """Returns is_error=True without raising an exception."""

    def __init__(self) -> None:
        super().__init__(
            name="soft_error",
            description="Reports a soft error",
            parameters_schema={
                "type": "object",
                "properties": {"input": {"type": "string"}},
            },
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return ToolExecutionResult(content="soft fail", is_error=True)


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def echo_test_tool() -> _EchoTestTool:
    return _EchoTestTool()


@pytest.fixture
def failing_tool() -> _FailingTool:
    return _FailingTool()


@pytest.fixture
def no_schema_tool() -> _NoSchemaTool:
    return _NoSchemaTool()


@pytest.fixture
def strict_schema_tool() -> _StrictSchemaTool:
    return _StrictSchemaTool()


@pytest.fixture
def soft_error_tool() -> _SoftErrorTool:
    return _SoftErrorTool()


@pytest.fixture
def sample_registry(
    echo_test_tool: _EchoTestTool,
    failing_tool: _FailingTool,
    no_schema_tool: _NoSchemaTool,
    strict_schema_tool: _StrictSchemaTool,
    soft_error_tool: _SoftErrorTool,
) -> ToolRegistry:
    return ToolRegistry(
        [
            echo_test_tool,
            failing_tool,
            no_schema_tool,
            strict_schema_tool,
            soft_error_tool,
        ],
    )


@pytest.fixture
def sample_invoker(sample_registry: ToolRegistry) -> ToolInvoker:
    return ToolInvoker(sample_registry)


@pytest.fixture
def sample_tool_call() -> ToolCall:
    return ToolCall(
        id="call_001",
        name="echo_test",
        arguments={"message": "hello"},
    )
