"""List directory tool — lists entries in a workspace directory."""

import asyncio
import itertools
import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any, Final

from synthorg.core.enums import ActionType
from synthorg.observability import get_logger
from synthorg.observability.events.tool import (
    TOOL_FS_ERROR,
    TOOL_FS_GLOB_REJECTED,
    TOOL_FS_LIST,
    TOOL_FS_STAT_FAILED,
)
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.file_system._base_fs_tool import BaseFileSystemTool

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)

MAX_ENTRIES: Final[int] = 1000

# Reject glob patterns that could traverse above the workspace.
_UNSAFE_GLOB_RE = re.compile(r"(^|[/\\])\.\.([/\\]|$)|^\.\.$")


def _classify_entry(
    entry: Path,
    resolved: Path,
    workspace_root: Path,
    *,
    recursive: bool,
) -> str | None:
    """Classify a single directory entry into a display line.

    Returns a formatted string (e.g. ``[DIR]  name/``) or ``None``
    if the entry should be skipped (outside workspace, non-symlink).
    """
    display = str(entry.relative_to(resolved)) if recursive else entry.name

    entry_resolved = entry.resolve()
    if not entry_resolved.is_relative_to(workspace_root):
        if entry.is_symlink():
            return f"[SYMLINK] {display} -> (outside workspace)"
        return None

    if entry.is_symlink():
        return f"[SYMLINK] {display}"

    if entry.is_dir():
        return f"[DIR] {display}/"

    try:
        size = entry.stat().st_size
    except OSError as stat_exc:
        logger.warning(
            TOOL_FS_STAT_FAILED,
            path=str(entry),
            error=str(stat_exc),
        )
        return f"[FILE] {display} (unknown bytes)"
    return f"[FILE] {display} ({size} bytes)"


def _list_sync(
    resolved: Path,
    workspace_root: Path,
    pattern: str | None,
    *,
    recursive: bool,
) -> tuple[list[str], bool]:
    """Collect directory entries synchronously.

    Returns:
        A tuple of ``(lines, raw_capped)`` where *lines* are
        formatted strings with type prefixes and *raw_capped* is
        ``True`` when the raw scan hit the ``MAX_ENTRIES`` limit
        (meaning the directory may contain more entries).
    """
    glob_pattern = pattern or "*"
    if recursive:
        raw_iter = resolved.rglob(glob_pattern)
    elif pattern:
        raw_iter = resolved.glob(glob_pattern)
    else:
        raw_iter = resolved.iterdir()

    entries = sorted(itertools.islice(raw_iter, MAX_ENTRIES + 1))
    raw_capped = len(entries) > MAX_ENTRIES

    lines: list[str] = []
    for entry in entries[:MAX_ENTRIES]:
        try:
            line = _classify_entry(
                entry,
                resolved,
                workspace_root,
                recursive=recursive,
            )
            if line is not None:
                lines.append(line)
        except OSError as exc:
            logger.warning(
                TOOL_FS_ERROR,
                path=str(entry),
                error=str(exc),
            )
            lines.append(f"[ERROR] {entry.name} ({exc})")

    return lines, raw_capped


class ListDirectoryTool(BaseFileSystemTool):
    """Lists files and directories within the workspace.

    Supports optional glob filtering and recursive listing.  Output is
    sorted alphabetically with type prefixes (``[DIR]`` / ``[FILE]`` /
    ``[SYMLINK]`` / ``[ERROR]``).
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
            action_type=ActionType.CODE_READ,
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

    def _validate_list_args(
        self,
        pattern: str | None,
        *,
        recursive: bool,
    ) -> ToolExecutionResult | None:
        """Return an error result if the glob pattern is invalid."""
        if not pattern:
            return None
        if _UNSAFE_GLOB_RE.search(pattern):
            logger.warning(TOOL_FS_GLOB_REJECTED, pattern=pattern)
            return ToolExecutionResult(
                content=f"Unsafe glob pattern rejected: {pattern}",
                is_error=True,
            )
        if (
            PurePosixPath(pattern).is_absolute()
            or PureWindowsPath(pattern).is_absolute()
        ):
            logger.warning(TOOL_FS_GLOB_REJECTED, pattern=pattern)
            return ToolExecutionResult(
                content=f"Unsafe glob pattern rejected: {pattern}",
                is_error=True,
            )
        if "**" in pattern and not recursive:
            logger.warning(TOOL_FS_GLOB_REJECTED, pattern=pattern)
            return ToolExecutionResult(
                content=(
                    f"Pattern {pattern!r} uses '**' but recursive=False; "
                    "set recursive=True to use recursive globs"
                ),
                is_error=True,
            )
        return None

    def _resolve_and_check_dir(
        self,
        user_path: str,
    ) -> Path | ToolExecutionResult:
        """Resolve the path and verify it is an existing directory."""
        try:
            resolved = self.path_validator.validate(user_path)
        except ValueError as exc:
            return ToolExecutionResult(content=str(exc), is_error=True)
        if not resolved.exists():
            logger.warning(TOOL_FS_ERROR, path=user_path, error="not_found")
            return ToolExecutionResult(
                content=f"Path not found: {user_path}",
                is_error=True,
            )
        if not resolved.is_dir():
            logger.warning(
                TOOL_FS_ERROR,
                path=user_path,
                error="not_a_directory",
            )
            return ToolExecutionResult(
                content=f"Not a directory: {user_path}",
                is_error=True,
            )
        return resolved

    @staticmethod
    def _format_listing_result(
        user_path: str,
        lines: list[str],
        *,
        raw_capped: bool,
    ) -> tuple[str, dict[str, Any]]:
        """Build output text and metadata from listing lines.

        Args:
            user_path: The user-supplied directory path.
            lines: Classified entry lines from ``_list_sync``.
            raw_capped: Whether the raw filesystem scan hit the
                ``MAX_ENTRIES`` limit (directory may contain more).
        """
        total = len(lines)
        dir_count = sum(1 for ln in lines if ln.startswith("[DIR]"))
        file_count = sum(1 for ln in lines if ln.startswith("[FILE]"))
        if not lines:
            output = f"Directory is empty: {user_path}"
        else:
            output = "\n".join(lines)
            if raw_capped:
                output += (
                    f"\n\n[Truncated: showing {total} entries;"
                    " directory may contain more]"
                )
        metadata = {
            "path": user_path,
            "total_entries": total,
            "directories": dir_count,
            "files": file_count,
            "truncated": raw_capped,
        }
        return output, metadata

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """List directory contents.

        Args:
            arguments: Optionally contains ``path``, ``pattern``,
                and ``recursive``.

        Returns:
            A ``ToolExecutionResult`` with the listing or an error.
        """
        user_path: str = arguments.get("path", ".")
        pattern: str | None = arguments.get("pattern")
        recursive: bool = arguments.get("recursive", False)

        if err := self._validate_list_args(pattern, recursive=recursive):
            return err

        resolved_or_err = self._resolve_and_check_dir(user_path)
        if isinstance(resolved_or_err, ToolExecutionResult):
            return resolved_or_err

        try:
            lines, raw_capped = await asyncio.to_thread(
                _list_sync,
                resolved_or_err,
                self.workspace_root,
                pattern,
                recursive=recursive,
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
            logger.warning(TOOL_FS_ERROR, path=user_path, error=str(exc))
            return ToolExecutionResult(
                content=f"OS error listing directory: {user_path}",
                is_error=True,
            )

        output, metadata = self._format_listing_result(
            user_path,
            lines,
            raw_capped=raw_capped,
        )
        logger.info(
            TOOL_FS_LIST,
            path=user_path,
            total_entries=metadata["total_entries"],
            directories=metadata["directories"],
            files=metadata["files"],
        )
        return ToolExecutionResult(content=output, metadata=metadata)
