"""Delete file tool — removes a single file from the workspace."""

import asyncio
from typing import TYPE_CHECKING, Any

from synthorg.core.enums import ActionType
from synthorg.observability import get_logger
from synthorg.observability.events.tool import TOOL_FS_DELETE, TOOL_FS_ERROR
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.file_system._base_fs_tool import BaseFileSystemTool

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)


def _delete_sync(resolved: Path) -> int:
    """Delete file synchronously, returning its size before deletion.

    Raises:
        FileNotFoundError: If the file does not exist.
        IsADirectoryError: If the path is a directory.
        PermissionError: If the process lacks delete permission.
        OSError: For other OS-level errors.
    """
    if resolved.is_dir():
        msg = f"Is a directory: '{resolved}'"
        raise IsADirectoryError(msg)
    size = resolved.stat().st_size
    resolved.unlink()
    return size


class DeleteFileTool(BaseFileSystemTool):
    """Deletes a single file within the workspace.

    Directories cannot be deleted with this tool — only regular files.
    The ``require_elevated`` property is defined for future use by the
    engine's permission system (not yet enforced).

    Examples:
        Delete a file::

            tool = DeleteFileTool(workspace_root=Path("/ws"))
            result = await tool.execute(arguments={"path": "tmp.txt"})
    """

    def __init__(self, *, workspace_root: Path) -> None:
        """Initialize the delete-file tool.

        Args:
            workspace_root: Root directory bounding file access.
        """
        super().__init__(
            workspace_root=workspace_root,
            name="delete_file",
            action_type=ActionType.CODE_DELETE,
            description="Delete a single file from the workspace.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to workspace",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    @property
    def require_elevated(self) -> bool:
        """Whether this tool requires elevated permissions.

        Indicates this tool requires explicit approval before execution
        due to its destructive nature.  Not yet consumed by the engine;
        defined for forward-compatibility.
        """
        return True

    @staticmethod
    def _handle_delete_error(
        exc: OSError,
        user_path: str,
    ) -> ToolExecutionResult:
        """Map a delete OS error to a ``ToolExecutionResult``."""
        if isinstance(exc, FileNotFoundError):
            logger.warning(TOOL_FS_ERROR, path=user_path, error="not_found")
            return ToolExecutionResult(
                content=f"File not found: {user_path}",
                is_error=True,
            )
        if isinstance(exc, IsADirectoryError):
            logger.warning(
                TOOL_FS_ERROR,
                path=user_path,
                error="is_directory",
            )
            return ToolExecutionResult(
                content=f"Cannot delete directory (use a dedicated tool): {user_path}",
                is_error=True,
            )
        if isinstance(exc, PermissionError):
            logger.warning(
                TOOL_FS_ERROR,
                path=user_path,
                error="permission_denied",
            )
            return ToolExecutionResult(
                content=f"Permission denied: {user_path}",
                is_error=True,
            )
        logger.warning(TOOL_FS_ERROR, path=user_path, error=str(exc))
        return ToolExecutionResult(
            content=f"OS error deleting file: {user_path}",
            is_error=True,
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Delete a file from the workspace.

        Args:
            arguments: Must contain ``path``.

        Returns:
            A ``ToolExecutionResult`` confirming deletion or an error.
        """
        user_path: str = arguments["path"]

        try:
            resolved = self.path_validator.validate(user_path)
        except ValueError as exc:
            return ToolExecutionResult(content=str(exc), is_error=True)

        try:
            size_bytes = await asyncio.to_thread(_delete_sync, resolved)
        except OSError as exc:
            return self._handle_delete_error(exc, user_path)

        logger.info(
            TOOL_FS_DELETE,
            path=user_path,
            size_bytes=size_bytes,
        )
        return ToolExecutionResult(
            content=f"Deleted {user_path} ({size_bytes} bytes)",
            metadata={
                "path": user_path,
                "size_bytes": size_bytes,
            },
        )
