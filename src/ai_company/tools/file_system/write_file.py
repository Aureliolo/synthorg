"""Write file tool — creates or overwrites files in the workspace."""

import asyncio
import os
import pathlib
import tempfile
from typing import TYPE_CHECKING, Any, Final

from ai_company.observability import get_logger
from ai_company.observability.events.tool import (
    TOOL_FS_ERROR,
    TOOL_FS_SIZE_EXCEEDED,
    TOOL_FS_WRITE,
)
from ai_company.tools.base import ToolExecutionResult
from ai_company.tools.file_system._base_fs_tool import BaseFileSystemTool

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)

MAX_WRITE_SIZE_BYTES: Final[int] = 10_485_760  # 10 MB


def _write_sync(resolved: Path, content: str, *, create_dirs: bool) -> tuple[int, bool]:
    """Write content to file synchronously.

    Uses an atomic write pattern (temp file + replace) so that a crash
    or disk-full during the write does not corrupt an existing file.

    Args:
        resolved: Resolved file path within the workspace.
        content: Text content to write.
        create_dirs: Whether to create parent directories.

    Returns:
        Tuple of (bytes_written, created) where *created* is True if
        the file did not exist before the write.

    Raises:
        IsADirectoryError: If the target is a directory.
        PermissionError: If the process lacks write permission.
        OSError: For other OS-level I/O failures.
    """
    created = not resolved.exists()
    if create_dirs:
        resolved.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(resolved.parent),
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        pathlib.Path(tmp_path).replace(resolved)
    except BaseException:
        pathlib.Path(tmp_path).unlink(missing_ok=True)
        raise

    return resolved.stat().st_size, created


class WriteFileTool(BaseFileSystemTool):
    """Creates or overwrites a file within the workspace.

    Optionally creates parent directories when ``create_directories``
    is True.

    Examples:
        Write a new file::

            tool = WriteFileTool(workspace_root=Path("/ws"))
            result = await tool.execute(
                arguments={"path": "out.txt", "content": "hello"}
            )
    """

    def __init__(self, *, workspace_root: Path) -> None:
        """Initialize the write-file tool.

        Args:
            workspace_root: Root directory bounding file access.
        """
        super().__init__(
            workspace_root=workspace_root,
            name="write_file",
            description=(
                "Write content to a file, creating or overwriting it. "
                "Set create_directories to true to create parent dirs."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to workspace",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write",
                    },
                    "create_directories": {
                        "type": "boolean",
                        "description": (
                            "Create parent directories if missing (default false)"
                        ),
                        "default": False,
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        )

    async def execute(  # noqa: PLR0911
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Write content to a file.

        Args:
            arguments: Must contain ``path`` and ``content``; optionally
                ``create_directories``.

        Returns:
            A ``ToolExecutionResult`` confirming the write or an error.
        """
        user_path: str = arguments["path"]
        content: str = arguments["content"]
        create_dirs: bool = arguments.get("create_directories", False)

        content_size = len(content.encode("utf-8"))
        if content_size > MAX_WRITE_SIZE_BYTES:
            logger.warning(
                TOOL_FS_SIZE_EXCEEDED,
                path=user_path,
                size_bytes=content_size,
                max_bytes=MAX_WRITE_SIZE_BYTES,
            )
            return ToolExecutionResult(
                content=(
                    f"Content too large to write: {content_size:,} bytes "
                    f"(max {MAX_WRITE_SIZE_BYTES:,})"
                ),
                is_error=True,
            )

        try:
            if create_dirs:
                resolved = self.path_validator.validate(user_path)
            else:
                resolved = self.path_validator.validate_parent_exists(user_path)
        except ValueError as exc:
            return ToolExecutionResult(content=str(exc), is_error=True)

        # Explicit is_dir() check: on Windows, write_text() on a directory
        # raises PermissionError (not IsADirectoryError).
        if resolved.is_dir():
            logger.warning(TOOL_FS_ERROR, path=user_path, error="is_directory")
            return ToolExecutionResult(
                content=f"Path is a directory, not a file: {user_path}",
                is_error=True,
            )

        try:
            bytes_written, created = await asyncio.to_thread(
                _write_sync, resolved, content, create_dirs=create_dirs
            )
        except IsADirectoryError:
            logger.warning(TOOL_FS_ERROR, path=user_path, error="is_directory")
            return ToolExecutionResult(
                content=f"Path is a directory, not a file: {user_path}",
                is_error=True,
            )
        except PermissionError:
            logger.warning(TOOL_FS_ERROR, path=user_path, error="permission_denied")
            return ToolExecutionResult(
                content=f"Permission denied: {user_path}",
                is_error=True,
            )
        except OSError as exc:
            logger.warning(TOOL_FS_ERROR, path=user_path, error=str(exc))
            return ToolExecutionResult(
                content=f"OS error writing file: {user_path}",
                is_error=True,
            )

        action = "Created" if created else "Updated"
        logger.info(
            TOOL_FS_WRITE,
            path=user_path,
            bytes_written=bytes_written,
            created=created,
        )

        return ToolExecutionResult(
            content=f"{action} {user_path} ({bytes_written} bytes)",
            metadata={
                "path": user_path,
                "bytes_written": bytes_written,
                "created": created,
            },
        )
