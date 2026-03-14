"""Unit test fixtures for the tool system."""

import asyncio
from typing import Any

import pytest

from synthorg.core.enums import ToolAccessLevel, ToolCategory
from synthorg.providers.models import ToolCall
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.permissions import ToolPermissionChecker
from synthorg.tools.registry import ToolRegistry

# ── Concrete test tools (private to tests) ────────────────────────


class _EchoTestTool(BaseTool):
    """Returns arguments as content."""

    def __init__(self) -> None:
        super().__init__(
            name="echo_test",
            description="Echoes arguments back",
            category=ToolCategory.CODE_EXECUTION,
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
            category=ToolCategory.CODE_EXECUTION,
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
            category=ToolCategory.CODE_EXECUTION,
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
            category=ToolCategory.CODE_EXECUTION,
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
            category=ToolCategory.CODE_EXECUTION,
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


class _RecursionTool(BaseTool):
    """Raises RecursionError in execute."""

    def __init__(self) -> None:
        super().__init__(
            name="recursion",
            description="Raises RecursionError",
            category=ToolCategory.CODE_EXECUTION,
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
        msg = "maximum recursion depth"
        raise RecursionError(msg)


class _InvalidSchemaTool(BaseTool):
    """Tool with an invalid JSON Schema (properties is not a dict)."""

    def __init__(self) -> None:
        super().__init__(
            name="invalid_schema",
            description="Has invalid schema",
            category=ToolCategory.CODE_EXECUTION,
            parameters_schema={"type": "object", "properties": "not_a_dict"},
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return ToolExecutionResult(content="ok")


class _EmptyErrorTool(BaseTool):
    """Raises exception with empty string message."""

    def __init__(self) -> None:
        super().__init__(
            name="empty_error",
            description="Raises with empty message",
            category=ToolCategory.CODE_EXECUTION,
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
        msg = ""
        raise ValueError(msg)


class _MutatingTool(BaseTool):
    """Tool that mutates its arguments to test boundary isolation."""

    def __init__(self) -> None:
        super().__init__(
            name="mutating",
            description="Mutates args",
            category=ToolCategory.CODE_EXECUTION,
            parameters_schema={
                "type": "object",
                "properties": {
                    "nested": {"type": "object"},
                },
            },
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        arguments["injected"] = True
        if "nested" in arguments:
            arguments["nested"]["mutated"] = True
        return ToolExecutionResult(content="mutated")


class _RemoteRefTool(BaseTool):
    """Tool with a remote ``$ref`` in its schema (for SSRF testing)."""

    def __init__(self) -> None:
        super().__init__(
            name="remote_ref",
            description="Has remote ref in schema",
            category=ToolCategory.CODE_EXECUTION,
            parameters_schema={
                "type": "object",
                "properties": {
                    "data": {"$ref": "http://evil.example.com/schema.json"},
                },
            },
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return ToolExecutionResult(content="ok")


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


@pytest.fixture
def extended_invoker() -> ToolInvoker:
    """Invoker with echo, recursion, invalid-schema, empty-error,
    remote-ref, and mutating tools for edge-case tests.
    """
    tools = [
        _EchoTestTool(),
        _RecursionTool(),
        _InvalidSchemaTool(),
        _EmptyErrorTool(),
        _RemoteRefTool(),
        _MutatingTool(),
    ]
    return ToolInvoker(ToolRegistry(tools))


# ── Concurrency test tools ───────────────────────────────────────


class _DelayTool(BaseTool):
    """Sleeps for ``delay`` seconds, then returns ``value``."""

    def __init__(self) -> None:
        super().__init__(
            name="delay",
            description="Sleeps then returns value",
            category=ToolCategory.CODE_EXECUTION,
            parameters_schema={
                "type": "object",
                "properties": {
                    "delay": {"type": "number"},
                    "value": {"type": "string"},
                },
                "required": ["delay", "value"],
                "additionalProperties": False,
            },
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        await asyncio.sleep(arguments["delay"])
        return ToolExecutionResult(content=arguments["value"])


class _ConcurrencyTrackingTool(BaseTool):
    """Tracks peak concurrent executions via a lock-guarded counter."""

    def __init__(self) -> None:
        super().__init__(
            name="tracking",
            description="Tracks concurrency",
            category=ToolCategory.CODE_EXECUTION,
            parameters_schema={
                "type": "object",
                "properties": {
                    "duration": {"type": "number"},
                },
                "required": ["duration"],
                "additionalProperties": False,
            },
        )
        self._lock = asyncio.Lock()
        self._current = 0
        self._peak = 0

    @property
    def peak(self) -> int:
        """Return the peak concurrent execution count."""
        return self._peak

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        async with self._lock:
            self._current += 1
            self._peak = max(self._peak, self._current)
        await asyncio.sleep(arguments["duration"])
        async with self._lock:
            self._current -= 1
        return ToolExecutionResult(content=str(self._peak))


@pytest.fixture
def concurrency_tracking_tool() -> _ConcurrencyTrackingTool:
    """Standalone tracking tool for direct peak inspection."""
    return _ConcurrencyTrackingTool()


@pytest.fixture
def concurrency_invoker(
    concurrency_tracking_tool: _ConcurrencyTrackingTool,
) -> ToolInvoker:
    """Invoker with echo, failing, delay, tracking, and recursion tools."""
    tools: list[BaseTool] = [
        _EchoTestTool(),
        _FailingTool(),
        _DelayTool(),
        concurrency_tracking_tool,
        _RecursionTool(),
    ]
    return ToolInvoker(ToolRegistry(tools))


# ── Categorized tool for permission tests ────────────────────────


class _CategorizedTool(BaseTool):
    """Minimal tool with configurable name and category."""

    def __init__(
        self,
        *,
        name: str,
        category: ToolCategory = ToolCategory.OTHER,
    ) -> None:
        super().__init__(
            name=name,
            description=f"Test tool: {name}",
            category=category,
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return ToolExecutionResult(content="ok")


# ── Permission fixtures ──────────────────────────────────────────


@pytest.fixture
def permission_registry() -> ToolRegistry:
    """Registry with tools spanning multiple categories."""
    return ToolRegistry(
        [
            _CategorizedTool(name="fs_tool", category=ToolCategory.FILE_SYSTEM),
            _CategorizedTool(name="code_tool", category=ToolCategory.CODE_EXECUTION),
            _CategorizedTool(name="web_tool", category=ToolCategory.WEB),
            _CategorizedTool(name="deploy_tool", category=ToolCategory.DEPLOYMENT),
            _CategorizedTool(name="terminal_tool", category=ToolCategory.TERMINAL),
            _CategorizedTool(name="other_tool", category=ToolCategory.OTHER),
        ]
    )


@pytest.fixture
def permission_checker() -> ToolPermissionChecker:
    """Standard-level checker with no explicit allow/deny lists."""
    return ToolPermissionChecker(access_level=ToolAccessLevel.STANDARD)


@pytest.fixture
def permission_invoker(
    permission_registry: ToolRegistry,
    permission_checker: ToolPermissionChecker,
) -> ToolInvoker:
    """Invoker with standard-level permission checker."""
    return ToolInvoker(permission_registry, permission_checker=permission_checker)
