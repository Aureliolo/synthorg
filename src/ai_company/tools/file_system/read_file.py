"""Read file tool — reads file content from the workspace.

Supports optional line-range selection and enforces a maximum
file-size guard to prevent loading excessively large files into memory.
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
from ai_company.tools.file_system._base_fs_tool import (
    BaseFileSystemTool,
    _map_os_error,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)

MAX_FILE_SIZE_BYTES: Final[int] = 1_048_576  # 1 MB


def _read_sync(
    resolved: Path,
    start: int | None,
    end: int | None,
    *,
    max_bytes: int | None = None,
) -> str:
    """Read file content synchronously, with optional line slicing.

    Args:
        resolved: Resolved file path within the workspace.
        start: First line to return (1-based inclusive), or ``None``.
        end: Last line to return (1-based inclusive), or ``None``.
        max_bytes: When set, only the first *max_bytes* characters are
            read via text-mode ``read()`` (approximate byte cap for
            oversized files without a line range).

    Returns:
        The file content (possibly sliced or truncated).

    Raises:
        UnicodeDecodeError: If the file contains non-UTF-8 bytes.
        FileNotFoundError: If the file does not exist.
        PermissionError: If the process lacks read permission.
        OSError: For other OS-level I/O failures.
    """
    if start is not None or end is not None:
        raw = resolved.read_text(encoding="utf-8")
        lines = raw.splitlines(keepends=True)
        s = (start - 1) if start is not None else 0
        e = end if end is not None else len(lines)
        return "".join(lines[s:e])

    if max_bytes is not None:
        with resolved.open(encoding="utf-8") as fh:
            return fh.read(max_bytes)

    return resolved.read_text(encoding="utf-8")


class ReadFileTool(BaseFileSystemTool):
    """Reads the content of a file within the workspace.

    Supports optional ``start_line`` / ``end_line`` for partial reads.
    Files exceeding 1 MB are read in bounded fashion: when no line
    range is specified only the first 1 MB is returned (with a
    truncation notice).  Binary (non-UTF-8) files produce an error.

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

    async def execute(  # noqa: PLR0911
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

        if start_line is not None and end_line is not None and start_line > end_line:
            return ToolExecutionResult(
                content=(
                    f"Invalid line range: start_line ({start_line}) "
                    f"must be <= end_line ({end_line})"
                ),
                is_error=True,
            )

        try:
            resolved = self.path_validator.validate(user_path)
        except ValueError as exc:
            return ToolExecutionResult(content=str(exc), is_error=True)

        # Explicit is_dir() check: on Windows, stat() succeeds on dirs
        # and read_text() raises PermissionError (not IsADirectoryError).
        if resolved.is_dir():
            logger.warning(TOOL_FS_ERROR, path=user_path, error="is_directory")
            return ToolExecutionResult(
                content=f"Path is a directory, not a file: {user_path}",
                is_error=True,
            )

        try:
            stat_result = await asyncio.to_thread(resolved.stat)
            size_bytes = stat_result.st_size
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError) as exc:
            log_key, msg = _map_os_error(exc, user_path, "reading")
            logger.warning(TOOL_FS_ERROR, path=user_path, error=log_key)
            return ToolExecutionResult(content=msg, is_error=True)

        oversized = size_bytes > MAX_FILE_SIZE_BYTES
        has_line_range = start_line is not None or end_line is not None

        if oversized:
            logger.warning(
                TOOL_FS_SIZE_EXCEEDED,
                path=user_path,
                size_bytes=size_bytes,
                max_bytes=MAX_FILE_SIZE_BYTES,
            )
            if has_line_range:
                return ToolExecutionResult(
                    content=(
                        f"File too large for line-range read: "
                        f"{user_path} ({size_bytes:,} bytes, "
                        f"max {MAX_FILE_SIZE_BYTES:,})"
                    ),
                    is_error=True,
                )

        # When oversized, read only MAX_FILE_SIZE_BYTES to avoid
        # loading the whole file into memory.
        max_bytes = MAX_FILE_SIZE_BYTES if oversized else None

        try:
            content = await asyncio.to_thread(
                _read_sync,
                resolved,
                start_line,
                end_line,
                max_bytes=max_bytes,
            )
        except UnicodeDecodeError:
            logger.warning(TOOL_FS_BINARY_DETECTED, path=user_path)
            return ToolExecutionResult(
                content=f"Cannot read binary file: {user_path}",
                is_error=True,
            )
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError) as exc:
            log_key, msg = _map_os_error(exc, user_path, "reading")
            logger.warning(TOOL_FS_ERROR, path=user_path, error=log_key)
            return ToolExecutionResult(content=msg, is_error=True)

        line_count = content.count("\n") + (
            1 if content and not content.endswith("\n") else 0
        )

        if oversized:
            content += (
                f"\n\n[Truncated: file is {size_bytes:,} bytes, "
                f"showing first {MAX_FILE_SIZE_BYTES:,}]"
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
