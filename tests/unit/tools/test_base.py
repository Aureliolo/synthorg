"""Tests for BaseTool ABC and ToolExecutionResult."""

from typing import Any

import pytest
from pydantic import ValidationError

from synthorg.core.enums import ToolCategory
from synthorg.providers.models import ToolDefinition
from synthorg.tools.base import BaseTool, ToolExecutionResult

pytestmark = pytest.mark.timeout(30)


# ── ToolExecutionResult ──────────────────────────────────────────


@pytest.mark.unit
class TestToolExecutionResult:
    """Tests for ToolExecutionResult model."""

    def test_defaults(self) -> None:
        result = ToolExecutionResult(content="output")
        assert result.content == "output"
        assert result.is_error is False
        assert result.metadata == {}

    def test_custom_values(self) -> None:
        result = ToolExecutionResult(
            content="error output",
            is_error=True,
            metadata={"code": 42},
        )
        assert result.content == "error output"
        assert result.is_error is True
        assert result.metadata == {"code": 42}

    def test_frozen(self) -> None:
        result = ToolExecutionResult(content="output")
        with pytest.raises(ValidationError):
            result.content = "modified"  # type: ignore[misc]


# ── BaseTool ─────────────────────────────────────────────────────


class _ConcreteTool(BaseTool):
    """Minimal concrete tool for testing BaseTool."""

    def __init__(
        self,
        *,
        name: str = "test_tool",
        description: str = "A test tool",
        parameters_schema: dict[str, Any] | None = None,
        category: ToolCategory = ToolCategory.CODE_EXECUTION,
    ) -> None:
        super().__init__(
            name=name,
            description=description,
            parameters_schema=parameters_schema,
            category=category,
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return ToolExecutionResult(content="executed")


@pytest.mark.unit
class TestBaseTool:
    """Tests for BaseTool ABC."""

    def test_properties(self) -> None:
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        tool = _ConcreteTool(
            name="my_tool",
            description="desc",
            parameters_schema=schema,
        )
        assert tool.name == "my_tool"
        assert tool.description == "desc"
        assert tool.parameters_schema == schema

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            _ConcreteTool(name="")

    def test_whitespace_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            _ConcreteTool(name="   ")

    def test_default_description_empty(self) -> None:
        tool = _ConcreteTool(name="t", description="")
        assert tool.description == ""

    def test_default_schema_none(self) -> None:
        tool = _ConcreteTool(name="t")
        assert tool.parameters_schema is None

    def test_schema_isolated_on_construction(self) -> None:
        props: dict[str, Any] = {"x": {"type": "string"}}
        schema: dict[str, Any] = {"type": "object", "properties": props}
        tool = _ConcreteTool(name="t", parameters_schema=schema)
        schema["injected"] = True
        assert tool.parameters_schema is not None
        assert "injected" not in tool.parameters_schema

    def test_schema_nested_isolated_on_construction(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
        }
        tool = _ConcreteTool(name="t", parameters_schema=schema)
        schema["properties"]["x"]["type"] = "integer"
        assert tool.parameters_schema is not None
        assert tool.parameters_schema["properties"]["x"]["type"] == "string"

    def test_schema_internal_is_read_only(self) -> None:
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        tool = _ConcreteTool(name="t", parameters_schema=schema)
        with pytest.raises(TypeError):
            tool._parameters_schema["injected"] = True  # type: ignore[index]

    def test_schema_property_returns_copy(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
        }
        tool = _ConcreteTool(name="t", parameters_schema=schema)
        returned = tool.parameters_schema
        assert returned is not None
        returned["injected"] = True
        assert tool.parameters_schema is not None
        assert "injected" not in tool.parameters_schema

    def test_schema_property_nested_mutation_isolated(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
        }
        tool = _ConcreteTool(name="t", parameters_schema=schema)
        returned = tool.parameters_schema
        assert returned is not None
        returned["properties"]["x"]["type"] = "integer"
        fresh = tool.parameters_schema
        assert fresh is not None
        assert fresh["properties"]["x"]["type"] == "string"

    def test_to_definition(self) -> None:
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        tool = _ConcreteTool(
            name="my_tool",
            description="desc",
            parameters_schema=schema,
        )
        defn = tool.to_definition()
        assert isinstance(defn, ToolDefinition)
        assert defn.name == "my_tool"
        assert defn.description == "desc"
        assert defn.parameters_schema == schema

    def test_to_definition_no_schema(self) -> None:
        tool = _ConcreteTool(name="t")
        defn = tool.to_definition()
        assert defn.parameters_schema == {}

    def test_execute_is_abstract(self) -> None:
        assert getattr(BaseTool.execute, "__isabstractmethod__", False) is True

    async def test_execute_runs(self) -> None:
        tool = _ConcreteTool(name="t")
        result = await tool.execute(arguments={})
        assert result.content == "executed"
