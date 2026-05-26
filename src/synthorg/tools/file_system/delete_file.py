"""Delete file tool: removes a single file from the workspace."""

import asyncio
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel  # noqa: TC002 -- ClassVar type at runtime

from synthorg.core.enums import ActionType
from synthorg.observability import get_logger
from synthorg.observability.events.tool import TOOL_FS_DELETE, TOOL_FS_ERROR
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.file_system._args import DeleteFileArgs
from synthorg.tools.file_system._base_fs_tool import BaseFileSystemTool, _map_os_error

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)

_DIRECTORY_HINT: str = "use a dedicated tool for directory removal"


def _delete_sync(resolved: Path) -> int:
    """Delete file synchronously, returning its size before deletion.

    Returns:
        Result of type ``int``.

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

    Directories cannot be deleted with this tool -- only regular files.
    The ``require_elevated`` property is defined for future use by the
    engine's permission system (not yet enforced).

    Examples:
        Delete a file::

            tool = DeleteFileTool(workspace_root=Path("/ws"))
            result = await tool.execute(arguments={"path": "tmp.txt"})
    """

    args_model: ClassVar[type[BaseModel] | None] = DeleteFileArgs

    def __init__(self, *, workspace_root: Path) -> None:
        """Initialize the delete-file tool, deriving its schema from DeleteFileArgs."""
        super().__init__(
            workspace_root=workspace_root,
            name="delete_file",
            action_type=ActionType.CODE_DELETE,
            description="Delete a single file from the workspace.",
            parameters_schema=DeleteFileArgs.model_json_schema(),
        )

    @property
    def require_elevated(self) -> bool:
        """Whether this tool requires elevated permissions.

        Indicates this tool requires explicit approval before execution
        due to its destructive nature.  Not yet consumed by the engine;
        defined for forward-compatibility.
        """
        return True

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
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError) as exc:
            log_key, msg = _map_os_error(
                exc,
                user_path,
                "deleting",
                dedicated_tool_hint=_DIRECTORY_HINT,
            )
            logger.warning(TOOL_FS_ERROR, path=user_path, error=log_key)
            return ToolExecutionResult(content=msg, is_error=True)

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
