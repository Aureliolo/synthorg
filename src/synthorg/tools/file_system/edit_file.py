"""Edit file tool — search-and-replace within workspace files."""

import asyncio
import os
import pathlib
import tempfile
from typing import TYPE_CHECKING, Any, Final

from synthorg.core.enums import ActionType
from synthorg.observability import get_logger
from synthorg.observability.events.tool import (
    TOOL_FS_EDIT,
    TOOL_FS_EDIT_NOT_FOUND,
    TOOL_FS_ERROR,
    TOOL_FS_NOOP,
    TOOL_FS_SIZE_EXCEEDED,
)
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.file_system._base_fs_tool import (
    BaseFileSystemTool,
    _map_os_error,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)

MAX_EDIT_FILE_SIZE_BYTES: Final[int] = 1_048_576  # 1 MB


def _edit_sync(resolved: Path, old_text: str, new_text: str) -> int:
    """Perform search-and-replace synchronously.

    Uses an atomic write pattern (temp file + replace) so that a crash
    or disk-full during the write does not corrupt the original file.

    Args:
        resolved: Resolved file path within the workspace.
        old_text: Non-empty text to search for.
        new_text: Replacement text (may be empty to delete).

    Returns:
        Number of occurrences of *old_text* found in the file.

    Raises:
        UnicodeDecodeError: If the file contains non-UTF-8 bytes.
        FileNotFoundError: If the file does not exist.
        PermissionError: If the process lacks read/write permission.
        OSError: For other OS-level I/O failures.
    """
    content = resolved.read_text(encoding="utf-8")
    count = content.count(old_text)
    if count > 0:
        new_content = content.replace(old_text, new_text, 1)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(resolved.parent),
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(new_content)
                fh.flush()
                os.fsync(fh.fileno())
            pathlib.Path(tmp_path).replace(resolved)
        except BaseException:
            pathlib.Path(tmp_path).unlink(missing_ok=True)
            raise
    return count


class EditFileTool(BaseFileSystemTool):
    """Replaces the first occurrence of ``old_text`` with ``new_text``.

    If ``old_text`` is not found, returns an error indicating that the
    text was not found.  When multiple occurrences exist, only the first
    is replaced and a warning is included in the output.

    Returns immediately with no change if ``old_text`` and ``new_text``
    are identical.

    Examples:
        Replace text::

            tool = EditFileTool(workspace_root=Path("/ws"))
            result = await tool.execute(
                arguments={
                    "path": "main.py",
                    "old_text": "foo",
                    "new_text": "bar",
                }
            )
    """

    def __init__(self, *, workspace_root: Path) -> None:
        """Initialize the edit-file tool.

        Args:
            workspace_root: Root directory bounding file access.
        """
        super().__init__(
            workspace_root=workspace_root,
            name="edit_file",
            action_type=ActionType.CODE_WRITE,
            description=(
                "Replace the first occurrence of old_text with new_text "
                "in a file. Use empty new_text to delete text."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to workspace",
                    },
                    "old_text": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Exact text to find",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Replacement text (empty string to delete)",
                    },
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
        )

    def _validate_edit_args(
        self,
        user_path: str,
        old_text: str,
        new_text: str,
    ) -> ToolExecutionResult | None:
        """Return an early error result if args are invalid, else None."""
        if not old_text:
            return ToolExecutionResult(
                content="old_text cannot be empty",
                is_error=True,
            )
        if old_text == new_text:
            logger.debug(
                TOOL_FS_NOOP,
                path=user_path,
                reason="old_text_equals_new_text",
            )
            return ToolExecutionResult(
                content=f"No change needed in {user_path}: "
                "old_text and new_text are identical",
                metadata={
                    "path": user_path,
                    "occurrences_found": 0,
                    "occurrences_replaced": 0,
                },
            )
        return None

    async def _preflight_check_file(
        self,
        user_path: str,
        resolved: Path,
    ) -> ToolExecutionResult | None:
        """Verify the file is editable (exists, not a dir, not too large)."""
        if resolved.is_dir():  # noqa: ASYNC240
            logger.warning(TOOL_FS_ERROR, path=user_path, error="is_directory")
            return ToolExecutionResult(
                content=f"Path is a directory, not a file: {user_path}",
                is_error=True,
            )
        try:
            stat_result = await asyncio.to_thread(resolved.stat)
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError) as exc:
            log_key, msg = _map_os_error(exc, user_path, "editing")
            logger.warning(TOOL_FS_ERROR, path=user_path, error=log_key)
            return ToolExecutionResult(content=msg, is_error=True)
        if stat_result.st_size > MAX_EDIT_FILE_SIZE_BYTES:
            logger.warning(
                TOOL_FS_SIZE_EXCEEDED,
                path=user_path,
                size_bytes=stat_result.st_size,
                max_bytes=MAX_EDIT_FILE_SIZE_BYTES,
            )
            return ToolExecutionResult(
                content=(
                    f"File too large to edit: {user_path} "
                    f"({stat_result.st_size:,} bytes, "
                    f"max {MAX_EDIT_FILE_SIZE_BYTES:,})"
                ),
                is_error=True,
            )
        return None

    async def _perform_edit(
        self,
        user_path: str,
        resolved: Path,
        old_text: str,
        new_text: str,
    ) -> ToolExecutionResult:
        """Run the edit and return the result."""
        try:
            count = await asyncio.to_thread(
                _edit_sync,
                resolved,
                old_text,
                new_text,
            )
        except UnicodeDecodeError:
            logger.warning(TOOL_FS_ERROR, path=user_path, error="binary")
            return ToolExecutionResult(
                content=f"Cannot edit binary file: {user_path}",
                is_error=True,
            )
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError) as exc:
            log_key, msg = _map_os_error(exc, user_path, "editing")
            logger.warning(TOOL_FS_ERROR, path=user_path, error=log_key)
            return ToolExecutionResult(content=msg, is_error=True)
        if count == 0:
            logger.warning(
                TOOL_FS_EDIT_NOT_FOUND,
                path=user_path,
                old_text_len=len(old_text),
            )
            return ToolExecutionResult(
                content=f"Text not found in {user_path}.",
                is_error=True,
                metadata={
                    "path": user_path,
                    "occurrences_found": 0,
                    "occurrences_replaced": 0,
                },
            )
        msg = f"Replaced 1 occurrence in {user_path}"
        if count > 1:
            msg += f" (warning: {count} total occurrences found, only first replaced)"
        logger.info(
            TOOL_FS_EDIT,
            path=user_path,
            occurrences_found=count,
            occurrences_replaced=1,
        )
        return ToolExecutionResult(
            content=msg,
            metadata={
                "path": user_path,
                "occurrences_found": count,
                "occurrences_replaced": 1,
            },
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Edit a file by replacing text.

        Args:
            arguments: Must contain ``path``, ``old_text``, ``new_text``.

        Returns:
            A ``ToolExecutionResult`` confirming the edit or an error.
        """
        user_path: str = arguments["path"]
        old_text: str = arguments["old_text"]
        new_text: str = arguments["new_text"]

        if err := self._validate_edit_args(user_path, old_text, new_text):
            return err

        try:
            resolved = self.path_validator.validate(user_path)
        except ValueError as exc:
            return ToolExecutionResult(content=str(exc), is_error=True)

        if err := await self._preflight_check_file(user_path, resolved):
            return err

        return await self._perform_edit(
            user_path,
            resolved,
            old_text,
            new_text,
        )
