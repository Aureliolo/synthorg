"""Base class for file system tools.

Provides the common ``ToolCategory.FILE_SYSTEM`` category and a
``PathValidator`` instance bound to the workspace root.
"""

from abc import ABC
from typing import TYPE_CHECKING, Any

from synthorg.core.enums import ToolCategory
from synthorg.tools.base import BaseTool
from synthorg.tools.file_system._path_validator import PathValidator

if TYPE_CHECKING:
    from pathlib import Path


def _map_os_error(exc: OSError, user_path: str, verb: str) -> tuple[str, str]:
    """Map an OS error to ``(log_key, user_message)`` for FS operations.

    Args:
        exc: The caught OS-level exception.
        user_path: The original user-supplied path string.
        verb: Action verb for the fallback message
            (e.g. ``"reading"``, ``"editing"``).

    Returns:
        A two-tuple of (structured log key, human-readable message).
    """
    if isinstance(exc, FileNotFoundError):
        return "not_found", f"File not found: {user_path}"
    if isinstance(exc, IsADirectoryError):
        return "is_directory", f"Path is a directory, not a file: {user_path}"
    if isinstance(exc, PermissionError):
        return "permission_denied", f"Permission denied: {user_path}"
    return "os_error", f"OS error {verb} file '{user_path}': {exc}"


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
        action_type: str | None = None,
    ) -> None:
        """Initialize with a workspace root and tool metadata.

        Args:
            workspace_root: Root directory bounding file access.
            name: Tool name.
            description: Human-readable description.
            parameters_schema: JSON Schema for tool parameters.
            action_type: Security action type override.
        """
        super().__init__(
            name=name,
            description=description,
            category=ToolCategory.FILE_SYSTEM,
            parameters_schema=parameters_schema,
            action_type=action_type,
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
