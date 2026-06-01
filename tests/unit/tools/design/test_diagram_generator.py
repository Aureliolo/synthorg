"""Tests for the diagram generator tool."""

from typing import Any, cast

import pytest

from synthorg.core.enums import ActionType, ToolCategory
from synthorg.tools.design.diagram_generator import DiagramGeneratorTool


@pytest.mark.unit
class TestDiagramGeneratorTool:
    """Tests for DiagramGeneratorTool."""

    @pytest.mark.parametrize(
        ("attr", "expected"),
        [
            ("category", ToolCategory.DESIGN),
            ("action_type", ActionType.DOCS_WRITE),
            ("name", "diagram_generator"),
        ],
        ids=["category", "action_type", "name"],
    )
    def test_tool_attributes(self, attr: str, expected: object) -> None:
        tool = DiagramGeneratorTool()
        assert getattr(tool, attr) == expected

    @pytest.mark.parametrize(
        ("diagram_type", "description", "expected_keyword"),
        [
            ("flowchart", "A --> B\nB --> C", "flowchart TD"),
            ("sequence", "Alice->>Bob: Hello", "sequenceDiagram"),
            ("class", "Animal <|-- Duck", "classDiagram"),
            ("state", "[*] --> Active", "stateDiagram-v2"),
            ("architecture", "A --> B", "flowchart TD"),
        ],
    )
    async def test_execute_mermaid_diagram_types(
        self,
        diagram_type: str,
        description: str,
        expected_keyword: str,
    ) -> None:
        tool = DiagramGeneratorTool()
        result = await tool.execute(
            arguments={
                "diagram_type": diagram_type,
                "description": description,
            }
        )
        assert not result.is_error
        assert expected_keyword in result.content
        assert result.metadata["diagram_type"] == diagram_type
        assert result.metadata["output_format"] == "mermaid"

    async def test_execute_with_title(self) -> None:
        tool = DiagramGeneratorTool()
        result = await tool.execute(
            arguments={
                "diagram_type": "flowchart",
                "description": "A --> B",
                "title": "My Diagram",
            }
        )
        assert not result.is_error
        assert 'title: "My Diagram"' in result.content

    async def test_execute_graphviz(self) -> None:
        tool = DiagramGeneratorTool()
        result = await tool.execute(
            arguments={
                "diagram_type": "flowchart",
                "description": "A -> B",
                "output_format": "graphviz",
            }
        )
        assert not result.is_error
        assert "digraph" in result.content
        assert "A -> B" in result.content

    async def test_execute_graphviz_with_title(self) -> None:
        tool = DiagramGeneratorTool()
        result = await tool.execute(
            arguments={
                "diagram_type": "flowchart",
                "description": "A -> B",
                "output_format": "graphviz",
                "title": "Test",
            }
        )
        assert not result.is_error
        assert 'label="Test"' in result.content

    async def test_graphviz_title_escapes_quotes(self) -> None:
        tool = DiagramGeneratorTool()
        result = await tool.execute(
            arguments={
                "diagram_type": "flowchart",
                "description": "A -> B",
                "output_format": "graphviz",
                "title": 'My "Quoted" Title',
            }
        )
        assert not result.is_error
        assert r"My \"Quoted\" Title" in result.content
        # No unescaped double quotes that would break DOT syntax
        assert 'label="My \\"Quoted\\" Title"' in result.content
        assert 'label="My "Quoted" Title"' not in result.content

    @pytest.mark.parametrize(
        ("args", "expected_msg"),
        [
            (
                {"diagram_type": "invalid", "description": "test"},
                "Invalid diagram_type",
            ),
            (
                {
                    "diagram_type": "flowchart",
                    "description": "test",
                    "output_format": "pdf",
                },
                "Invalid output_format",
            ),
        ],
        ids=["invalid_diagram_type", "invalid_output_format"],
    )
    async def test_execute_invalid_inputs(
        self, args: dict[str, Any], expected_msg: str
    ) -> None:
        tool = DiagramGeneratorTool()
        result = await tool.execute(arguments=args)
        assert result.is_error
        assert expected_msg in result.content

    async def test_execute_architecture_uses_graph_in_graphviz(
        self,
    ) -> None:
        tool = DiagramGeneratorTool()
        result = await tool.execute(
            arguments={
                "diagram_type": "architecture",
                "description": "A -- B",
                "output_format": "graphviz",
            }
        )
        assert not result.is_error
        assert result.content.startswith("graph ")

    def test_parameters_schema_requires_type_and_description(
        self,
    ) -> None:
        tool = DiagramGeneratorTool()
        schema = tool.parameters_schema
        assert schema is not None
        required = cast("dict[str, Any]", schema)["required"]
        assert "diagram_type" in required
        assert "description" in required
