# mypy: disable-error-code="explicit-override"
"""Tests for BaseTool progressive disclosure integration."""

from typing import Any

import pytest

from synthorg.core.enums import ToolCategory
from synthorg.core.tool_disclosure import ToolL1Metadata, ToolL2Body, ToolL3Resource
from synthorg.tools.base import BaseTool, ToolExecutionResult


class _DisclosureTool(BaseTool):
    """Concrete tool for testing disclosure methods."""

    def __init__(
        self,
        *,
        name: str = "test_tool",
        description: str = "A test tool for testing",
        parameters_schema: dict[str, Any] | None = None,
        category: ToolCategory = ToolCategory.FILE_SYSTEM,
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


class _CustomDisclosureTool(BaseTool):
    """Tool with custom L1/L2/L3 overrides."""

    def __init__(self) -> None:
        super().__init__(
            name="custom_tool",
            description="A custom tool",
            parameters_schema={"type": "object", "properties": {}},
            category=ToolCategory.WEB,
        )

    def to_l1_metadata(self) -> ToolL1Metadata:
        return ToolL1Metadata(
            name=self.name,
            short_description="Custom short desc",
            category="web",
            typical_cost_tier="expensive",
        )

    def to_l2_body(self) -> ToolL2Body:
        return ToolL2Body(
            full_description="Custom full description with details",
            parameter_schema={"type": "object"},
            usage_examples=("example 1", "example 2"),
            failure_modes=("timeout",),
        )

    def get_l3_resources(self) -> tuple[ToolL3Resource, ...]:
        content = "# Resource content"
        return (
            ToolL3Resource(
                resource_id="guide",
                content_type="markdown",
                content=content,
                size_bytes=len(content.encode()),
            ),
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return ToolExecutionResult(content="custom executed")


# ── Default L1 derivation ───────────────────────────────────────


@pytest.mark.unit
class TestDefaultL1Metadata:
    """Tests for default to_l1_metadata() derivation."""

    def test_derives_from_existing_fields(self) -> None:
        tool = _DisclosureTool(
            name="read_file",
            description="Read a file from the workspace",
            category=ToolCategory.FILE_SYSTEM,
        )
        l1 = tool.to_l1_metadata()
        assert l1.name == "read_file"
        assert l1.short_description == "Read a file from the workspace"
        assert l1.category == "file_system"
        assert l1.typical_cost_tier == "medium"

    def test_truncates_long_description(self) -> None:
        tool = _DisclosureTool(description="x" * 300)
        l1 = tool.to_l1_metadata()
        assert len(l1.short_description) == 200

    def test_returns_new_instance_each_call(self) -> None:
        tool = _DisclosureTool()
        l1_a = tool.to_l1_metadata()
        l1_b = tool.to_l1_metadata()
        assert l1_a == l1_b
        assert l1_a is not l1_b


# ── Default L2 derivation ───────────────────────────────────────


@pytest.mark.unit
class TestDefaultL2Body:
    """Tests for default to_l2_body() derivation."""

    def test_derives_from_existing_fields(self) -> None:
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        tool = _DisclosureTool(
            description="Full description here",
            parameters_schema=schema,
        )
        l2 = tool.to_l2_body()
        assert l2.full_description == "Full description here"
        assert l2.parameter_schema == schema
        assert l2.usage_examples == ()
        assert l2.failure_modes == ()

    def test_no_schema_uses_empty_dict(self) -> None:
        tool = _DisclosureTool(parameters_schema=None)
        l2 = tool.to_l2_body()
        assert l2.parameter_schema == {}


# ── Default L3 resources ────────────────────────────────────────


@pytest.mark.unit
class TestDefaultL3Resources:
    """Tests for default get_l3_resources()."""

    def test_default_returns_empty(self) -> None:
        tool = _DisclosureTool()
        assert tool.get_l3_resources() == ()


# ── Custom overrides ────────────────────────────────────────────


@pytest.mark.unit
class TestCustomDisclosure:
    """Tests for tools with custom L1/L2/L3 overrides."""

    def test_custom_l1_metadata(self) -> None:
        tool = _CustomDisclosureTool()
        l1 = tool.to_l1_metadata()
        assert l1.short_description == "Custom short desc"
        assert l1.typical_cost_tier == "expensive"

    def test_custom_l2_body(self) -> None:
        tool = _CustomDisclosureTool()
        l2 = tool.to_l2_body()
        assert l2.full_description == "Custom full description with details"
        assert len(l2.usage_examples) == 2
        assert len(l2.failure_modes) == 1

    def test_custom_l3_resources(self) -> None:
        tool = _CustomDisclosureTool()
        resources = tool.get_l3_resources()
        assert len(resources) == 1
        assert resources[0].resource_id == "guide"
        assert resources[0].content_type == "markdown"


# ── to_definition() integration ─────────────────────────────────


@pytest.mark.unit
class TestToDefinitionDisclosure:
    """Tests for to_definition() with disclosure fields."""

    def test_populates_l1_metadata(self) -> None:
        tool = _DisclosureTool(name="my_tool", description="My tool desc")
        defn = tool.to_definition()
        assert defn.l1_metadata is not None
        assert defn.l1_metadata.name == "my_tool"
        assert defn.l1_metadata.short_description == "My tool desc"

    def test_populates_l2_body(self) -> None:
        schema = {"type": "object"}
        tool = _DisclosureTool(parameters_schema=schema)
        defn = tool.to_definition()
        assert defn.l2_body is not None
        assert defn.l2_body.parameter_schema == schema

    def test_populates_l3_resources(self) -> None:
        tool = _CustomDisclosureTool()
        defn = tool.to_definition()
        assert len(defn.l3_resources) == 1
        assert defn.l3_resources[0].resource_id == "guide"

    def test_default_l3_empty(self) -> None:
        tool = _DisclosureTool()
        defn = tool.to_definition()
        assert defn.l3_resources == ()

    def test_flat_fields_still_populated(self) -> None:
        tool = _DisclosureTool(
            name="my_tool",
            description="desc",
            parameters_schema={"type": "object"},
        )
        defn = tool.to_definition()
        assert defn.name == "my_tool"
        assert defn.description == "desc"
        assert defn.parameters_schema == {"type": "object"}
