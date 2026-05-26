"""Template formatter tool -- render message templates safely.

Uses Jinja2 ``SandboxedEnvironment`` for safe variable substitution
with no arbitrary code execution.
"""

from typing import Any, ClassVar, Final

from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel  # noqa: TC002 -- ClassVar type at runtime

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import ActionType
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.communication import (
    COMM_TOOL_TEMPLATE_RENDER_FAILED,
    COMM_TOOL_TEMPLATE_RENDER_INVALID,
    COMM_TOOL_TEMPLATE_RENDER_START,
    COMM_TOOL_TEMPLATE_RENDER_SUCCESS,
)
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.communication._args import TemplateFormatterArgs
from synthorg.tools.communication.base_communication_tool import (
    BaseCommunicationTool,
)
from synthorg.tools.communication.config import (
    CommunicationToolsConfig,  # noqa: TC001
)

logger = get_logger(__name__)

_OUTPUT_FORMATS: Final[frozenset[str]] = frozenset({"text", "html", "markdown"})


class TemplateFormatterTool(BaseCommunicationTool):
    """Format message templates with safe variable substitution.

    Uses Jinja2 ``SandboxedEnvironment`` to prevent arbitrary
    code execution.  Only inline templates are supported (no
    file-based templates) to avoid path traversal risks.

    Examples:
        Render a template::

            tool = TemplateFormatterTool()
            result = await tool.execute(
                arguments={
                    "template": "Hello {{ name }}, your balance is {{ amount }}.",
                    "variables": {
                        "name": "Alice",
                        "amount": "100 units",
                    },
                }
            )
    """

    args_model: ClassVar[type[BaseModel] | None] = TemplateFormatterArgs

    def __init__(
        self,
        *,
        config: CommunicationToolsConfig | None = None,
    ) -> None:
        """Initialize the template formatter tool.

        Args:
            config: Communication tool configuration with formatter
                size limits. ``None`` falls back to defaults.
        """
        super().__init__(
            name="template_formatter",
            description=(
                "Render inline message templates with safe "
                "Jinja2 variable substitution."
            ),
            parameters_schema=TemplateFormatterArgs.model_json_schema(),
            action_type=ActionType.CODE_READ,
            config=config,
        )
        self._env = SandboxedEnvironment()
        self._env_autoesc = SandboxedEnvironment(autoescape=True)

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Render a template with variables.

        Args:
            arguments: Must contain ``template`` and ``variables``;
                optionally ``format``.

        Returns:
            A ``ToolExecutionResult`` with rendered text.
        """
        template_str = arguments.get("template")
        variables = arguments.get("variables")
        if not isinstance(template_str, str):
            logger.warning(
                COMM_TOOL_TEMPLATE_RENDER_FAILED,
                error="missing_or_invalid_template",
            )
            return ToolExecutionResult(
                content="'template' must be a string.",
                is_error=True,
            )
        if not isinstance(variables, dict):
            logger.warning(
                COMM_TOOL_TEMPLATE_RENDER_FAILED,
                error="missing_or_invalid_variables",
            )
            return ToolExecutionResult(
                content="'variables' must be a dict.",
                is_error=True,
            )
        output_format = arguments.get("format", "text")
        if not isinstance(output_format, str):
            logger.warning(
                COMM_TOOL_TEMPLATE_RENDER_FAILED,
                error="invalid_format_type",
            )
            return ToolExecutionResult(
                content="'format' must be a string.",
                is_error=True,
            )

        if output_format not in _OUTPUT_FORMATS:
            logger.warning(
                COMM_TOOL_TEMPLATE_RENDER_FAILED,
                error="invalid_output_format",
                output_format=output_format,
            )
            return ToolExecutionResult(
                content=(
                    f"Invalid format: {output_format!r}. "
                    f"Must be one of: {sorted(_OUTPUT_FORMATS)}"
                ),
                is_error=True,
            )

        logger.info(
            COMM_TOOL_TEMPLATE_RENDER_START,
            template_length=len(template_str),
            variable_count=len(variables),
            output_format=output_format,
        )

        env = self._env_autoesc if output_format == "html" else self._env
        try:
            tmpl = env.from_string(template_str)
        except TemplateSyntaxError as exc:
            logger.warning(
                COMM_TOOL_TEMPLATE_RENDER_INVALID,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=f"Invalid template syntax: {safe_error_description(exc)}",
                is_error=True,
            )

        try:
            rendered = tmpl.render(**variables)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                COMM_TOOL_TEMPLATE_RENDER_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=f"Template rendering failed: {safe_error_description(exc)}",
                is_error=True,
            )

        logger.info(
            COMM_TOOL_TEMPLATE_RENDER_SUCCESS,
            output_length=len(rendered),
            output_format=output_format,
        )

        return ToolExecutionResult(
            content=rendered,
            metadata={
                "format": output_format,
                "output_length": len(rendered),
            },
        )
