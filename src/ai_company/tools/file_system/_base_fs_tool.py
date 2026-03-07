"""Base class for file system tools.

Provides the common ``ToolCategory.FILE_SYSTEM`` category and a
``PathValidator`` instance bound to the workspace root.
"""

from abc import ABC
from typing import TYPE_CHECKING, Any

from ai_company.core.enums import ToolCategory
from ai_company.tools.base import BaseTool
from ai_company.tools.file_system._path_validator import PathValidator

if TYPE_CHECKING:
    from pathlib import Path


class BaseFileSystemTool(BaseTool, ABC):
    """Abstract base for all file system tools.

    Sets ``category=ToolCategory.FILE_SYSTEM`` and holds a shared
    ``PathValidator`` for workspace-scoped path resolution.
    """

    def __init__(
        self,
        *,
        workspace_root: Path,
        name: str,
        description: str = "",
        parameters_schema: dict[str, Any] | None = None,
    ) -> None:
        """Initialize with a workspace root and tool metadata.

        Args:
            workspace_root: Root directory bounding file access.
            name: Tool name.
            description: Human-readable description.
            parameters_schema: JSON Schema for tool parameters.
        """
        super().__init__(
            name=name,
            description=description,
            category=ToolCategory.FILE_SYSTEM,
            parameters_schema=parameters_schema,
        )
        self._path_validator = PathValidator(workspace_root)

    @property
    def workspace_root(self) -> Path:
        """The resolved workspace root directory."""
        return self._path_validator.workspace_root

    @property
    def path_validator(self) -> PathValidator:
        """The path validator instance."""
        return self._path_validator
