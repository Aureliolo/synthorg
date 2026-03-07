"""List directory tool — lists entries in a workspace directory."""

import asyncio
import itertools
import re
from typing import TYPE_CHECKING, Any, Final

from ai_company.observability import get_logger
from ai_company.observability.events.tool import (
    TOOL_FS_ERROR,
    TOOL_FS_GLOB_REJECTED,
    TOOL_FS_LIST,
    TOOL_FS_STAT_FAILED,
)
from ai_company.tools.base import ToolExecutionResult
from ai_company.tools.file_system._base_fs_tool import BaseFileSystemTool

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)

MAX_ENTRIES: Final[int] = 1000

# Reject glob patterns that could traverse above the workspace.
_UNSAFE_GLOB_RE = re.compile(r"(^|[/\\])\.\.[/\\]|^\.\.$")


def _list_sync(
    resolved: Path,
    workspace_root: Path,
    pattern: str | None,
    *,
    recursive: bool,
) -> list[str]:
    """Collect directory entries synchronously."""
    glob_pattern = pattern or "*"
    if recursive:
        raw_iter = resolved.rglob(glob_pattern)
    elif pattern:
        raw_iter = resolved.glob(glob_pattern)
    else:
        raw_iter = resolved.iterdir()

    # Cap at MAX_ENTRIES + 1 to detect truncation without
    # materialising the entire iterator.
    entries = sorted(itertools.islice(raw_iter, MAX_ENTRIES + 1))

    lines: list[str] = []
    for entry in entries:
        try:
            display = str(entry.relative_to(resolved)) if recursive else entry.name

            if entry.is_symlink():
                target = entry.resolve()
                if not target.is_relative_to(workspace_root):
                    lines.append(f"[SYMLINK] {display} -> (outside workspace)")
                    continue

            if entry.is_dir():
                lines.append(f"[DIR]  {display}/")
            else:
                try:
                    size = entry.stat().st_size
                except OSError as stat_exc:
                    logger.warning(
                        TOOL_FS_STAT_FAILED,
                        path=str(entry),
                        error=str(stat_exc),
                    )
                    lines.append(f"[FILE] {display} (unknown bytes)")
                    continue
                lines.append(f"[FILE] {display} ({size} bytes)")
        except OSError as exc:
            logger.warning(
                TOOL_FS_ERROR,
                path=str(entry),
                error=str(exc),
            )
            lines.append(f"[ERROR] {entry.name}")

    return lines


class ListDirectoryTool(BaseFileSystemTool):
    """Lists files and directories within the workspace.

    Supports optional glob filtering and recursive listing.  Output is
    sorted alphabetically with type prefixes (``[DIR]`` / ``[FILE]``).
    Results are capped at ``MAX_ENTRIES`` (1000) entries to prevent
    excessive output.

    Examples:
        List current directory::

            tool = ListDirectoryTool(workspace_root=Path("/ws"))
            result = await tool.execute(arguments={})
    """

    def __init__(self, *, workspace_root: Path) -> None:
        """Initialize the list-directory tool.

        Args:
            workspace_root: Root directory bounding file access.
        """
        super().__init__(
            workspace_root=workspace_root,
            name="list_directory",
            description=(
                "List files and directories. Supports glob filtering "
                "and recursive listing."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            'Directory path relative to workspace (default ".")'
                        ),
                        "default": ".",
                    },
                    "pattern": {
                        "type": "string",
                        "description": 'Glob filter (e.g. "*.py")',
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Recursive listing (default false)",
                        "default": False,
                    },
                },
                "additionalProperties": False,
            },
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """List directory contents.

        Args:
            arguments: Optionally contains ``path``, ``pattern``, and
                ``recursive``.

        Returns:
            A ``ToolExecutionResult`` with the listing or an error.
        """
        user_path: str = arguments.get("path", ".")
        pattern: str | None = arguments.get("pattern")
        recursive: bool = arguments.get("recursive", False)

        # Reject glob patterns that could traverse above the workspace.
        if pattern and _UNSAFE_GLOB_RE.search(pattern):
            logger.warning(
                TOOL_FS_GLOB_REJECTED,
                pattern=pattern,
            )
            return ToolExecutionResult(
                content=f"Unsafe glob pattern rejected: {pattern}",
                is_error=True,
            )

        try:
            resolved = self.path_validator.validate(user_path)
        except ValueError as exc:
            return ToolExecutionResult(content=str(exc), is_error=True)

        if not resolved.is_dir():
            logger.warning(TOOL_FS_ERROR, path=user_path, error="not_a_directory")
            return ToolExecutionResult(
                content=f"Not a directory: {user_path}",
                is_error=True,
            )

        try:
            lines = await asyncio.to_thread(
                _list_sync,
                resolved,
                self.workspace_root,
                pattern,
                recursive=recursive,
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
                content=f"OS error listing directory: {user_path}",
                is_error=True,
            )

        truncated = len(lines) > MAX_ENTRIES
        if truncated:
            lines = lines[:MAX_ENTRIES]

        dir_count = sum(1 for ln in lines if ln.startswith("[DIR]"))
        file_count = sum(1 for ln in lines if ln.startswith("[FILE]"))

        if not lines:
            output = f"Directory is empty: {user_path}"
        else:
            output = "\n".join(lines)
            if truncated:
                output += (
                    f"\n\n[Truncated: showing first {MAX_ENTRIES} of more entries]"
                )

        logger.info(
            TOOL_FS_LIST,
            path=user_path,
            total_entries=len(lines),
            directories=dir_count,
            files=file_count,
        )

        return ToolExecutionResult(
            content=output,
            metadata={
                "path": user_path,
                "total_entries": len(lines),
                "directories": dir_count,
                "files": file_count,
            },
        )
