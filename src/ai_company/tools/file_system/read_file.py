"""Read file tool — reads file content from the workspace.

Supports optional line-range selection and enforces a maximum
file-size guard to prevent loading excessively large files.
"""

import asyncio
from typing import TYPE_CHECKING, Any, Final

from ai_company.observability import get_logger
from ai_company.observability.events.tool import (
    TOOL_FS_BINARY_DETECTED,
    TOOL_FS_ERROR,
    TOOL_FS_READ,
    TOOL_FS_SIZE_EXCEEDED,
)
from ai_company.tools.base import ToolExecutionResult
from ai_company.tools.file_system._base_fs_tool import BaseFileSystemTool

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)

MAX_FILE_SIZE_BYTES: Final[int] = 1_048_576  # 1 MB


def _read_sync(resolved: Path, start: int | None, end: int | None) -> str:
    """Read file content synchronously, with optional line slicing."""
    raw = resolved.read_text(encoding="utf-8")
    if start is not None or end is not None:
        lines = raw.splitlines(keepends=True)
        s = (start - 1) if start is not None else 0
        e = end if end is not None else len(lines)
        raw = "".join(lines[s:e])
    return raw


class ReadFileTool(BaseFileSystemTool):
    """Reads the content of a file within the workspace.

    Supports optional ``start_line`` / ``end_line`` for partial reads.
    Files exceeding 1 MB are truncated with a warning.  Binary files
    (non-UTF-8) produce an error result.

    Examples:
        Read an entire file::

            tool = ReadFileTool(workspace_root=Path("/ws"))
            result = await tool.execute(arguments={"path": "src/main.py"})
    """

    def __init__(self, *, workspace_root: Path) -> None:
        """Initialize the read-file tool.

        Args:
            workspace_root: Root directory bounding file access.
        """
        super().__init__(
            workspace_root=workspace_root,
            name="read_file",
            description=(
                "Read the contents of a file. Supports optional "
                "line-range selection via start_line and end_line."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to workspace",
                    },
                    "start_line": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "First line to read (1-based inclusive)",
                    },
                    "end_line": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Last line to read (1-based inclusive)",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Read a file and return its content.

        Args:
            arguments: Must contain ``path``; optionally ``start_line``
                and ``end_line``.

        Returns:
            A ``ToolExecutionResult`` with the file content or an error.
        """
        user_path: str = arguments["path"]
        start_line: int | None = arguments.get("start_line")
        end_line: int | None = arguments.get("end_line")

        try:
            resolved = self.path_validator.validate(user_path)
        except ValueError as exc:
            return ToolExecutionResult(content=str(exc), is_error=True)

        try:
            size = await asyncio.to_thread(resolved.stat)
            size_bytes = size.st_size

            if size_bytes > MAX_FILE_SIZE_BYTES:
                logger.warning(
                    TOOL_FS_SIZE_EXCEEDED,
                    path=user_path,
                    size_bytes=size_bytes,
                    max_bytes=MAX_FILE_SIZE_BYTES,
                )

            content = await asyncio.to_thread(
                _read_sync, resolved, start_line, end_line
            )

            if size_bytes > MAX_FILE_SIZE_BYTES and not (start_line or end_line):
                content = content[:MAX_FILE_SIZE_BYTES]
                content += (
                    f"\n\n[Truncated: file is {size_bytes:,} bytes, "
                    f"showing first {MAX_FILE_SIZE_BYTES:,}]"
                )

            line_count = content.count("\n") + (
                1 if content and not content.endswith("\n") else 0
            )

            logger.info(
                TOOL_FS_READ,
                path=user_path,
                size_bytes=size_bytes,
                line_count=line_count,
            )

            return ToolExecutionResult(
                content=content,
                metadata={
                    "path": user_path,
                    "size_bytes": size_bytes,
                    "line_count": line_count,
                },
            )
        except UnicodeDecodeError:
            logger.warning(TOOL_FS_BINARY_DETECTED, path=user_path)
            return ToolExecutionResult(
                content=f"Cannot read binary file: {user_path}",
                is_error=True,
            )
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError) as exc:
            _err_msgs: dict[type, tuple[str, str]] = {
                FileNotFoundError: (
                    "not_found",
                    f"File not found: {user_path}",
                ),
                IsADirectoryError: (
                    "is_directory",
                    f"Path is a directory, not a file: {user_path}",
                ),
                PermissionError: (
                    "permission_denied",
                    f"Permission denied: {user_path}",
                ),
            }
            log_key, msg = _err_msgs.get(
                type(exc),
                (str(exc), f"OS error reading file: {user_path}"),
            )
            logger.warning(TOOL_FS_ERROR, path=user_path, error=log_key)
            return ToolExecutionResult(content=msg, is_error=True)
