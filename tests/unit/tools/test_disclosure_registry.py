"""Tests for ToolRegistry disclosure-aware methods."""

from typing import override

import pytest

from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.registry import ToolRegistry


class _StubTool(BaseTool):
    """Minimal tool for registry tests."""

    def __init__(
        self,
        *,
        name: str = "stub",
        description: str = "A stub tool",
        category: ToolCategory = ToolCategory.FILE_SYSTEM,
    ) -> None:
        super().__init__(
            name=name,
            description=description,
            category=category,
        )

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        return ToolExecutionResult(content="ok")


@pytest.mark.unit
class TestToL1Summaries:
    """Tests for ToolRegistry.to_l1_summaries()."""

    def test_returns_l1_for_all_tools(self) -> None:
        tools = [
            _StubTool(name="alpha", description="Tool A"),
            _StubTool(name="beta", description="Tool B"),
        ]
        registry = ToolRegistry(tools)
        summaries = registry.to_l1_summaries()
        assert len(summaries) == 2
        names = [s.name for s in summaries]
        assert names == ["alpha", "beta"]  # sorted

    def test_l1_fields_populated(self) -> None:
        tool = _StubTool(
            name="read_file",
            description="Read a file from disk",
            category=ToolCategory.FILE_SYSTEM,
        )
        registry = ToolRegistry([tool])
        (summary,) = registry.to_l1_summaries()
        assert summary.name == "read_file"
        assert summary.short_description == "Read a file from disk"
        assert summary.category == "file_system"
        assert summary.typical_cost_tier == "medium"

    def test_empty_registry(self) -> None:
        registry = ToolRegistry([])
        assert registry.to_l1_summaries() == ()

    def test_definitions_still_include_l1(self) -> None:
        tool = _StubTool(name="tool_a")
        registry = ToolRegistry([tool])
        (defn,) = registry.to_definitions()
        assert defn.l1_metadata is not None
        assert defn.l1_metadata.name == "tool_a"
        assert defn.l2_body is not None
