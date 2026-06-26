"""Diagram generator tool -- generate Mermaid/Graphviz DSL from descriptions.

Produces diagram markup (Mermaid or Graphviz DOT) that can be rendered
by downstream tools or the web dashboard.  No external provider is
required -- the tool outputs DSL text directly.
"""

from typing import ClassVar, override

from pydantic import BaseModel

from synthorg.core.boundary import parse_typed
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.events.design import (
    DESIGN_DIAGRAM_GENERATION_FAILED,
    DESIGN_DIAGRAM_GENERATION_START,
    DESIGN_DIAGRAM_GENERATION_SUCCESS,
)
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.design._args import DiagramGeneratorArgs
from synthorg.tools.design.base_design_tool import BaseDesignTool
from synthorg.tools.design.config import DesignToolsConfig

logger = get_logger(__name__)


class DiagramGeneratorTool(BaseDesignTool):
    """Generate diagram markup (Mermaid/Graphviz) from structured descriptions.

    Produces DSL text that can be rendered by Mermaid.js, Graphviz,
    or the web dashboard.  No external API is needed.

    Examples:
        Generate a flowchart::

            tool = DiagramGeneratorTool()
            result = await tool.execute(
                arguments={
                    "diagram_type": "flowchart",
                    "description": "A -> B -> C",
                    "title": "Simple Flow",
                }
            )
    """

    args_model: ClassVar[type[BaseModel] | None] = DiagramGeneratorArgs

    def __init__(
        self,
        *,
        config: DesignToolsConfig | None = None,
    ) -> None:
        """Initialize the diagram generator tool.

        Args:
            config: Design tool configuration with diagram size
                limits. ``None`` falls back to defaults.
        """
        super().__init__(
            name="diagram_generator",
            description=(
                "Generate diagram markup (Mermaid or Graphviz) "
                "from structured descriptions."
            ),
            parameters_schema=DiagramGeneratorArgs.model_json_schema(),
            action_type=ActionType.DOCS_WRITE,
            config=config,
        )

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Generate diagram markup from a description.

        Args:
            arguments: Must contain ``diagram_type`` and
                ``description``; optionally ``title`` and
                ``output_format``.

        Returns:
            A ``ToolExecutionResult`` with the diagram DSL.
        """
        args = parse_typed("tool.execute", arguments, DiagramGeneratorArgs)
        diagram_type = args.diagram_type
        description = args.description
        title = args.title
        output_format = args.output_format

        logger.info(
            DESIGN_DIAGRAM_GENERATION_START,
            diagram_type=diagram_type,
            output_format=output_format,
            description_length=len(description),
        )

        try:
            if output_format == "mermaid":
                markup = self._generate_mermaid(diagram_type, description, title)
            else:
                markup = self._generate_graphviz(diagram_type, description, title)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                DESIGN_DIAGRAM_GENERATION_FAILED,
                error="internal_error",
                diagram_type=diagram_type,
            )
            return ToolExecutionResult(
                content="Diagram generation failed.",
                is_error=True,
            )

        logger.info(
            DESIGN_DIAGRAM_GENERATION_SUCCESS,
            diagram_type=diagram_type,
            output_format=output_format,
            markup_length=len(markup),
        )

        return ToolExecutionResult(
            content=markup,
            metadata={
                "diagram_type": diagram_type,
                "output_format": output_format,
                "title": title,
            },
        )

    @staticmethod
    def _generate_mermaid(
        diagram_type: str,
        description: str,
        title: str,
    ) -> str:
        """Generate Mermaid DSL from the description.

        Wraps the user-provided description in the appropriate
        Mermaid diagram directive.

        Args:
            diagram_type: Type of diagram.
            description: User-provided diagram specification.
            title: Optional title.

        Returns:
            Mermaid markup string.
        """
        type_map: dict[str, str] = {
            "flowchart": "flowchart TD",
            "sequence": "sequenceDiagram",
            "class": "classDiagram",
            "state": "stateDiagram-v2",
            "architecture": "flowchart TD",
        }
        directive = type_map.get(diagram_type, "flowchart TD")
        lines: list[str] = []
        if title:
            safe_title = (
                title.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\r", " ")
                .replace("\n", " ")
            )
            lines.append("---")
            lines.append(f'title: "{safe_title}"')
            lines.append("---")
        lines.append(directive)
        lines.extend(f"    {line}" for line in description.strip().splitlines())
        return "\n".join(lines)

    @staticmethod
    def _generate_graphviz(
        diagram_type: str,
        description: str,
        title: str,
    ) -> str:
        """Generate Graphviz DOT from the description.

        Wraps the user-provided description in a DOT digraph block.

        Args:
            diagram_type: Type of diagram (used for graph attributes).
            description: User-provided diagram specification.
            title: Optional title.

        Returns:
            Graphviz DOT string.
        """
        graph_type = "graph" if diagram_type == "architecture" else "digraph"
        if title:
            escaped = (
                title.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\r", "")
                .replace("\n", "\\n")
            )
            label = f'    label="{escaped}";\n'
        else:
            label = ""
        return f"{graph_type} {diagram_type} {{\n{label}    {description}\n}}"
