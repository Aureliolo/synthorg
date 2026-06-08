"""Base class for communication tools.

Provides the common ``ToolCategory.COMMUNICATION`` category and
a shared configuration reference.
"""

from abc import ABC

from pydantic import JsonValue

from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool
from synthorg.tools.communication.config import CommunicationToolsConfig


class BaseCommunicationTool(BaseTool, ABC):
    """Abstract base for all communication tools.

    Sets ``category=ToolCategory.COMMUNICATION`` and holds a shared
    ``CommunicationToolsConfig``.
    """

    def __init__(
        self,
        *,
        name: str,
        description: str = "",
        parameters_schema: dict[str, JsonValue] | None = None,
        action_type: str | None = None,
        config: CommunicationToolsConfig | None = None,
    ) -> None:
        """Initialize a communication tool with configuration.

        Args:
            name: Tool name.
            description: Human-readable description.
            parameters_schema: JSON Schema for tool parameters.
            action_type: Security action type override.
            config: Communication tool configuration.
        """
        super().__init__(
            name=name,
            description=description,
            category=ToolCategory.COMMUNICATION,
            parameters_schema=parameters_schema,
            action_type=action_type,
        )
        self._config = config or CommunicationToolsConfig()

    @property
    def config(self) -> CommunicationToolsConfig:
        """The communication tool configuration."""
        return self._config
