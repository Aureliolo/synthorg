"""Tests for CompactContextTool."""

from typing import cast

import pytest

from synthorg.providers.models import ToolDefinition
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.context.compact_context import CompactContextTool
from tests._shared import JsonDict


@pytest.mark.unit
class TestCompactContextTool:
    """Tests for CompactContextTool class."""

    def test_tool_name(self) -> None:
        """Tool name is 'compact_context'."""
        tool = CompactContextTool()
        assert tool.name == "compact_context"

    def test_tool_category(self) -> None:
        """Tool category is MEMORY."""
        tool = CompactContextTool()
        assert tool.category == ToolCategory.MEMORY

    def test_tool_description_not_empty(self) -> None:
        """Tool has a non-empty description."""
        tool = CompactContextTool()
        assert tool.description
        assert isinstance(tool.description, str)
        assert len(tool.description) > 0

    def test_parameters_schema_has_required_fields(self) -> None:
        """Parameters schema includes strategy and reason."""
        tool = CompactContextTool()
        schema = tool.parameters_schema
        assert schema is not None
        schema_d = cast(JsonDict, schema)
        assert "properties" in schema
        assert "strategy" in schema_d["properties"]
        assert "reason" in schema_d["properties"]
        assert "required" in schema
        assert "strategy" in schema_d["required"]
        assert "reason" in schema_d["required"]

    def test_preserve_markers_optional_in_schema(self) -> None:
        """preserve_markers is optional with default True."""
        tool = CompactContextTool()
        schema = tool.parameters_schema
        assert schema is not None
        schema_d = cast(JsonDict, schema)
        assert "preserve_markers" in schema_d["properties"]
        # Should NOT be in required list since it has a default
        assert "preserve_markers" not in schema_d["required"]
        # Schema should indicate default is True
        assert schema_d["properties"]["preserve_markers"]["default"] is True

    async def test_execute_valid_args(self) -> None:
        """Execute with valid arguments returns ToolExecutionResult."""
        tool = CompactContextTool()
        result = await tool.execute(
            arguments={
                "strategy": "summarize",
                "reason": "context at 90 percent fill",
            },
        )

        assert result.content
        assert isinstance(result.content, str)
        assert "Compaction directive accepted" in result.content

    async def test_execute_has_correct_metadata_keys(self) -> None:
        """Execute result metadata contains strategy, preserve_markers, reason."""
        tool = CompactContextTool()
        result = await tool.execute(
            arguments={
                "strategy": "summarize",
                "reason": "context at 90 percent fill",
            },
        )

        metadata = result.metadata
        assert "strategy" in metadata
        assert "preserve_markers" in metadata
        assert "reason" in metadata
        assert "compaction_directive" in metadata

    async def test_execute_preserve_markers_default_true(self) -> None:
        """When preserve_markers not provided, defaults to True."""
        tool = CompactContextTool()
        result = await tool.execute(
            arguments={
                "strategy": "summarize",
                "reason": "context at 90 percent fill",
            },
        )

        assert result.metadata["preserve_markers"] is True

    async def test_execute_preserve_markers_explicit_false(self) -> None:
        """When preserve_markers is False, it's preserved in metadata."""
        tool = CompactContextTool()
        result = await tool.execute(
            arguments={
                "strategy": "summarize",
                "reason": "context at 90 percent fill",
                "preserve_markers": False,
            },
        )

        assert result.metadata["preserve_markers"] is False

    async def test_execute_strategy_in_metadata(self) -> None:
        """Strategy from arguments is in metadata."""
        tool = CompactContextTool()
        result = await tool.execute(
            arguments={
                "strategy": "summarize",
                "reason": "context at 90 percent fill",
            },
        )

        assert result.metadata["strategy"] == "summarize"

    async def test_execute_reason_in_metadata(self) -> None:
        """Reason from arguments is in metadata."""
        tool = CompactContextTool()
        reason_text = "context at 90 percent fill"
        result = await tool.execute(
            arguments={
                "strategy": "summarize",
                "reason": reason_text,
            },
        )

        assert result.metadata["reason"] == reason_text

    async def test_execute_compaction_directive_true(self) -> None:
        """Metadata includes compaction_directive set to True."""
        tool = CompactContextTool()
        result = await tool.execute(
            arguments={
                "strategy": "summarize",
                "reason": "context at 90 percent fill",
            },
        )

        assert result.metadata["compaction_directive"] is True

    def test_to_definition_returns_valid_tool_definition(self) -> None:
        """to_definition() returns a valid ToolDefinition."""
        tool = CompactContextTool()
        definition = tool.to_definition()

        assert isinstance(definition, ToolDefinition)
        assert definition.name == "compact_context"
        assert definition.description
        assert definition.parameters_schema

    def test_to_definition_schema_is_correct(self) -> None:
        """ToolDefinition contains correct schema."""
        tool = CompactContextTool()
        definition = tool.to_definition()

        schema = cast(JsonDict, definition.parameters_schema)
        assert "properties" in schema
        assert "strategy" in schema["properties"]
        assert "reason" in schema["properties"]
        assert "strategy" in schema["required"]
        assert "reason" in schema["required"]

    async def test_execute_is_not_error(self) -> None:
        """Execute result is_error is False."""
        tool = CompactContextTool()
        result = await tool.execute(
            arguments={
                "strategy": "summarize",
                "reason": "context at 90 percent fill",
            },
        )

        assert result.is_error is False
