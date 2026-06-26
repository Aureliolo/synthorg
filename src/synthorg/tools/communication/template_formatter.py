"""Template formatter tool -- render message templates safely.

Uses Jinja2 ``SandboxedEnvironment`` for safe variable substitution
with no arbitrary code execution.
"""

from typing import ClassVar, override

from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel

from synthorg.core.boundary import parse_typed
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.communication import (
    COMM_TOOL_TEMPLATE_RENDER_FAILED,
    COMM_TOOL_TEMPLATE_RENDER_INVALID,
    COMM_TOOL_TEMPLATE_RENDER_START,
    COMM_TOOL_TEMPLATE_RENDER_SUCCESS,
)
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.communication._args import TemplateFormatterArgs
from synthorg.tools.communication.base_communication_tool import (
    BaseCommunicationTool,
)
from synthorg.tools.communication.config import (
    CommunicationToolsConfig,
)

logger = get_logger(__name__)


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

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Render a template with variables.

        Args:
            arguments: Must contain ``template`` and ``variables``;
                optionally ``format``.

        Returns:
            A ``ToolExecutionResult`` with rendered text.
        """
        args = parse_typed("tool.execute", arguments, TemplateFormatterArgs)
        template_str = args.template
        variables = args.variables
        output_format = args.format

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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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
