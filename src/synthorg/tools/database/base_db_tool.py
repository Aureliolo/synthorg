"""Base class for database tools.

Provides the common ``ToolCategory.DATABASE`` category and holds
the database connection configuration.
"""

from abc import ABC
from typing import Any

from synthorg.core.enums import ToolCategory
from synthorg.tools.base import BaseTool
from synthorg.tools.database.config import DatabaseConnectionConfig


class BaseDatabaseTool(BaseTool, ABC):
    """Abstract base for all database tools.

    Sets ``category=ToolCategory.DATABASE`` and holds a
    ``DatabaseConnectionConfig`` for connection management.
    """

    def __init__(
        self,
        *,
        name: str,
        description: str = "",
        parameters_schema: dict[str, Any] | None = None,
        action_type: str | None = None,
        config: DatabaseConnectionConfig,
    ) -> None:
        """Initialize a database tool with connection config.

        Args:
            name: Tool name.
            description: Human-readable description.
            parameters_schema: JSON Schema for tool parameters.
            action_type: Security action type override.
            config: Database connection configuration.
        """
        super().__init__(
            name=name,
            description=description,
            category=ToolCategory.DATABASE,
            parameters_schema=parameters_schema,
            action_type=action_type,
        )
        self._config = config

    @property
    def config(self) -> DatabaseConnectionConfig:
        """The database connection configuration."""
        return self._config
