"""Write file tool -- creates or overwrites files in the workspace."""

import asyncio
import os
import pathlib
import tempfile
from pathlib import Path
from typing import ClassVar, Final, cast, override

from pydantic import BaseModel

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.tool import (
    TOOL_FS_ERROR,
    TOOL_FS_SIZE_EXCEEDED,
    TOOL_FS_WRITE,
)
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.file_system._args import WriteFileArgs
from synthorg.tools.file_system._base_fs_tool import BaseFileSystemTool

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
        BaseException: Raised when the relevant invariant fails.
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

    args_model: ClassVar[type[BaseModel] | None] = WriteFileArgs

    def __init__(self, *, workspace_root: Path) -> None:
        """Initialize the write-file tool, deriving its schema from WriteFileArgs."""
        super().__init__(
            workspace_root=workspace_root,
            name="write_file",
            action_type=ActionType.CODE_WRITE,
            description=(
                "Write content to a file, creating or overwriting it. "
                "Set create_directories to true to create parent dirs."
            ),
            parameters_schema=WriteFileArgs.model_json_schema(),
        )

    def _validate_write_args(
        self,
        user_path: str,
        content: str,
    ) -> ToolExecutionResult | None:
        """Return an error if content exceeds the size limit.

        Returns:
            The resulting ``ToolExecutionResult``, or ``None`` when unavailable.
        """
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
        return None

    def _resolve_write_path(
        self,
        user_path: str,
        *,
        create_dirs: bool,
    ) -> Path | ToolExecutionResult:
        """Resolve and validate the write target path.

        Returns:
            Result of type ``Path | ToolExecutionResult``.
        """
        try:
            if create_dirs:
                resolved = self.path_validator.validate(user_path)
            else:
                resolved = self.path_validator.validate_parent_exists(
                    user_path,
                )
        except ValueError as exc:
            return ToolExecutionResult(content=str(exc), is_error=True)
        if resolved.is_dir():
            logger.warning(
                TOOL_FS_ERROR,
                path=user_path,
                error="is_directory",
            )
            return ToolExecutionResult(
                content=f"Path is a directory, not a file: {user_path}",
                is_error=True,
            )
        return resolved

    async def _perform_write(
        self,
        user_path: str,
        resolved: Path,
        content: str,
        *,
        create_dirs: bool,
    ) -> ToolExecutionResult:
        """Execute the write and build the result.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        try:
            bytes_written, created = await asyncio.to_thread(
                _write_sync,
                resolved,
                content,
                create_dirs=create_dirs,
            )
        except IsADirectoryError:
            logger.warning(
                TOOL_FS_ERROR,
                path=user_path,
                error="is_directory",
            )
            return ToolExecutionResult(
                content=f"Path is a directory, not a file: {user_path}",
                is_error=True,
            )
        except PermissionError:
            logger.warning(
                TOOL_FS_ERROR,
                path=user_path,
                error="permission_denied",
            )
            return ToolExecutionResult(
                content=f"Permission denied: {user_path}",
                is_error=True,
            )
        except OSError as exc:
            logger.warning(
                TOOL_FS_ERROR,
                path=user_path,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
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

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Write content to a file.

        Args:
            arguments: Must contain ``path`` and ``content``; optionally
                ``create_directories``.

        Returns:
            A ``ToolExecutionResult`` confirming the write or an error.
        """
        user_path = cast("str", arguments["path"])
        content = cast("str", arguments["content"])
        create_dirs = cast("bool", arguments.get("create_directories", False))

        if err := self._validate_write_args(user_path, content):
            return err

        resolved_or_err = self._resolve_write_path(
            user_path,
            create_dirs=create_dirs,
        )
        if isinstance(resolved_or_err, ToolExecutionResult):
            return resolved_or_err

        return await self._perform_write(
            user_path,
            resolved_or_err,
            content,
            create_dirs=create_dirs,
        )
