"""Tests for discovery tools (list_tools, load_tool, load_tool_resource)."""

import json
from typing import Any, cast, override

import pytest

from synthorg.core.enums import ToolCategory
from synthorg.core.tool_disclosure import ToolL1Metadata, ToolL2Body, ToolL3Resource
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.discovery import (
    ListToolsTool,
    LoadToolResourceTool,
    LoadToolTool,
    ToolDisclosureManager,
    build_discovery_tools,
)
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry

# ── Fixtures ────────────────────────────────────────────────────


class _ToolWithResources(BaseTool):
    """Tool with custom L1/L2/L3 for testing."""

    def __init__(self) -> None:
        super().__init__(
            name="rich_tool",
            description="A tool with resources",
            parameters_schema={
                "type": "object",
                "properties": {"x": {"type": "string"}},
            },
            category=ToolCategory.FILE_SYSTEM,
        )

    @override
    def to_l1_metadata(self) -> ToolL1Metadata:
        return ToolL1Metadata(
            name="rich_tool",
            short_description="A rich tool",
            category="file_system",
            typical_cost_tier="expensive",
        )

    @override
    def to_l2_body(self) -> ToolL2Body:
        return ToolL2Body(
            full_description="Full description of rich tool",
            parameter_schema={
                "type": "object",
                "properties": {"x": {"type": "string"}},
            },
            usage_examples=("example 1",),
            failure_modes=("timeout",),
        )

    @override
    def get_l3_resources(self) -> tuple[ToolL3Resource, ...]:
        content = "# Guide\nUsage guide here"
        return (
            ToolL3Resource(
                resource_id="guide",
                content_type="markdown",
                content=content,
                size_bytes=len(content.encode()),
            ),
        )

    @override
    async def execute(self, *, arguments: dict[str, Any]) -> ToolExecutionResult:
        return ToolExecutionResult(content="executed")


class _SimpleTool(BaseTool):
    """Simple tool without custom disclosure."""

    def __init__(self, *, name: str = "simple") -> None:
        super().__init__(
            name=name,
            description="A simple tool",
            category=ToolCategory.FILE_SYSTEM,
        )

    @override
    async def execute(self, *, arguments: dict[str, Any]) -> ToolExecutionResult:
        return ToolExecutionResult(content="ok")


def _make_invoker() -> ToolInvoker:
    """Build invoker with test tools."""
    tools: list[BaseTool] = [_ToolWithResources(), _SimpleTool()]
    return ToolInvoker(ToolRegistry(tools))


# ── ToolInvoker as ToolDisclosureManager ────────────────────────


@pytest.mark.unit
class TestInvokerAsDisclosureManager:
    """Verify ToolInvoker satisfies ToolDisclosureManager protocol."""

    def test_is_disclosure_manager(self) -> None:
        invoker = _make_invoker()
        assert isinstance(invoker, ToolDisclosureManager)

    def test_get_l2_body_found(self) -> None:
        invoker = _make_invoker()
        l2 = invoker.get_l2_body("rich_tool")
        assert l2 is not None
        assert l2.full_description == "Full description of rich tool"

    def test_get_l2_body_not_found(self) -> None:
        invoker = _make_invoker()
        assert invoker.get_l2_body("nonexistent") is None

    def test_get_l3_resource_found(self) -> None:
        invoker = _make_invoker()
        res = invoker.get_l3_resource("rich_tool", "guide")
        assert res is not None
        assert res.resource_id == "guide"
        assert res.content_type == "markdown"

    def test_get_l3_resource_not_found(self) -> None:
        invoker = _make_invoker()
        assert invoker.get_l3_resource("rich_tool", "nonexistent") is None

    def test_get_l3_resource_tool_not_found(self) -> None:
        invoker = _make_invoker()
        assert invoker.get_l3_resource("nonexistent", "guide") is None


# ── ListToolsTool ────────────────────────────────────────────────


@pytest.mark.unit
class TestListToolsTool:
    """Tests for ListToolsTool."""

    async def test_returns_all_l1_metadata(self) -> None:
        invoker = _make_invoker()
        tool = ListToolsTool(invoker)
        result = await tool.execute(arguments={})
        assert not result.is_error
        data = json.loads(result.content)
        names = {item["name"] for item in data}
        assert "rich_tool" in names
        assert "simple" in names

    async def test_metadata_includes_count(self) -> None:
        invoker = _make_invoker()
        tool = ListToolsTool(invoker)
        result = await tool.execute(arguments={})
        assert result.metadata["tool_count"] == 2

    async def test_l1_fields_present(self) -> None:
        invoker = _make_invoker()
        tool = ListToolsTool(invoker)
        result = await tool.execute(arguments={})
        data = json.loads(result.content)
        rich = next(item for item in data if item["name"] == "rich_tool")
        assert rich["short_description"] == "A rich tool"
        assert rich["category"] == "file_system"
        assert rich["typical_cost_tier"] == "expensive"


# ── LoadToolTool ─────────────────────────────────────────────────


@pytest.mark.unit
class TestLoadToolTool:
    """Tests for LoadToolTool."""

    async def test_returns_l2_body(self) -> None:
        invoker = _make_invoker()
        tool = LoadToolTool(invoker)
        result = await tool.execute(arguments={"tool_name": "rich_tool"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["name"] == "rich_tool"
        assert data["full_description"] == "Full description of rich tool"
        assert data["usage_examples"] == ["example 1"]
        assert data["failure_modes"] == ["timeout"]

    async def test_signals_should_load_tool(self) -> None:
        invoker = _make_invoker()
        tool = LoadToolTool(invoker)
        result = await tool.execute(arguments={"tool_name": "rich_tool"})
        assert result.metadata["should_load_tool"] == "rich_tool"

    async def test_not_found_returns_error(self) -> None:
        invoker = _make_invoker()
        tool = LoadToolTool(invoker)
        result = await tool.execute(arguments={"tool_name": "nonexistent"})
        assert result.is_error
        assert "not found" in result.content


# ── LoadToolResourceTool ─────────────────────────────────────────


@pytest.mark.unit
class TestLoadToolResourceTool:
    """Tests for LoadToolResourceTool."""

    async def test_returns_resource_payload(self) -> None:
        invoker = _make_invoker()
        tool = LoadToolResourceTool(invoker)
        result = await tool.execute(
            arguments={"tool_name": "rich_tool", "resource_id": "guide"},
        )
        assert not result.is_error
        payload = json.loads(result.content)
        assert payload["resource_id"] == "guide"
        assert payload["content_type"] == "markdown"
        assert "Usage guide here" in payload["content"]
        assert payload["size_bytes"] > 0

    async def test_signals_should_load_resource(self) -> None:
        invoker = _make_invoker()
        tool = LoadToolResourceTool(invoker)
        result = await tool.execute(
            arguments={"tool_name": "rich_tool", "resource_id": "guide"},
        )
        assert cast("dict[str, Any]", result.metadata)["should_load_resource"] == [
            "rich_tool",
            "guide",
        ]

    async def test_not_found_returns_error(self) -> None:
        invoker = _make_invoker()
        tool = LoadToolResourceTool(invoker)
        result = await tool.execute(
            arguments={"tool_name": "rich_tool", "resource_id": "nonexistent"},
        )
        assert result.is_error

    async def test_tool_not_found_returns_error(self) -> None:
        invoker = _make_invoker()
        tool = LoadToolResourceTool(invoker)
        result = await tool.execute(
            arguments={"tool_name": "nonexistent", "resource_id": "guide"},
        )
        assert result.is_error


# ── build_discovery_tools ────────────────────────────────────────


@pytest.mark.unit
class TestBuildDiscoveryTools:
    """Tests for build_discovery_tools factory."""

    def test_returns_three_tools(self) -> None:
        invoker = _make_invoker()
        tools = build_discovery_tools(invoker)
        assert len(tools) == 3
        names = {t.name for t in tools}
        assert names == {"list_tools", "load_tool", "load_tool_resource"}

    def test_all_are_base_tools(self) -> None:
        invoker = _make_invoker()
        for tool in build_discovery_tools(invoker):
            assert isinstance(tool, BaseTool)
