"""Base class for design tools.

Provides the common ``ToolCategory.DESIGN`` category and
a shared configuration reference.
"""

from abc import ABC

from pydantic import JsonValue

from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool
from synthorg.tools.design.config import DesignToolsConfig


class BaseDesignTool(BaseTool, ABC):
    """Abstract base for all design tools.

    Sets ``category=ToolCategory.DESIGN`` and holds a shared
    ``DesignToolsConfig``.
    """

    def __init__(
        self,
        *,
        name: str,
        description: str = "",
        parameters_schema: dict[str, JsonValue] | None = None,
        action_type: str | None = None,
        config: DesignToolsConfig | None = None,
    ) -> None:
        """Initialize a design tool with configuration.

        Args:
            name: Tool name.
            description: Human-readable description.
            parameters_schema: JSON Schema for tool parameters.
            action_type: Security action type override.
            config: Design tool configuration.
        """
        super().__init__(
            name=name,
            description=description,
            category=ToolCategory.DESIGN,
            parameters_schema=parameters_schema,
            action_type=action_type,
        )
        self._config = config or DesignToolsConfig()

    @property
    def config(self) -> DesignToolsConfig:
        """The design tool configuration."""
        return self._config
