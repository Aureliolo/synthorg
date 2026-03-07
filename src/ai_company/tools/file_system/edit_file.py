"""Edit file tool — search-and-replace within workspace files."""

import asyncio
from typing import TYPE_CHECKING, Any

from ai_company.observability import get_logger
from ai_company.observability.events.tool import (
    TOOL_FS_EDIT,
    TOOL_FS_EDIT_NOT_FOUND,
    TOOL_FS_ERROR,
)
from ai_company.tools.base import ToolExecutionResult
from ai_company.tools.file_system._base_fs_tool import BaseFileSystemTool

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)


def _edit_sync(resolved: Path, old_text: str, new_text: str) -> tuple[str, int]:
    """Perform search-and-replace synchronously.

    Returns:
        Tuple of (new_content, occurrences_found).
    """
    content = resolved.read_text(encoding="utf-8")
    count = content.count(old_text)
    if count > 0:
        new_content = content.replace(old_text, new_text, 1)
        resolved.write_text(new_content, encoding="utf-8")
    return content, count


class EditFileTool(BaseFileSystemTool):
    """Replaces the first occurrence of ``old_text`` with ``new_text``.

    If ``old_text`` is not found, returns an error with a snippet of the
    file content to help the LLM adjust its search string.  When
    multiple occurrences exist, only the first is replaced and a warning
    is included in the output.

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
                        "description": "Exact text to find",
                    },
                    "new_text": {
                        "type": "string",
                        "description": ("Replacement text (empty string to delete)"),
                    },
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Edit a file by replacing text.

        Args:
            arguments: Must contain ``path``, ``old_text``, and
                ``new_text``.

        Returns:
            A ``ToolExecutionResult`` confirming the edit or an error.
        """
        user_path: str = arguments["path"]
        old_text: str = arguments["old_text"]
        new_text: str = arguments["new_text"]

        if old_text == new_text:
            return ToolExecutionResult(
                content=f"No change needed in {user_path}: "
                "old_text and new_text are identical",
                metadata={
                    "path": user_path,
                    "occurrences_found": 0,
                    "occurrences_replaced": 0,
                },
            )

        try:
            resolved = self.path_validator.validate(user_path)
        except ValueError as exc:
            return ToolExecutionResult(content=str(exc), is_error=True)

        try:
            content, count = await asyncio.to_thread(
                _edit_sync, resolved, old_text, new_text
            )
        except UnicodeDecodeError:
            logger.warning(TOOL_FS_ERROR, path=user_path, error="binary")
            return ToolExecutionResult(
                content=f"Cannot edit binary file: {user_path}",
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
                (str(exc), f"OS error editing file: {user_path}"),
            )
            logger.warning(TOOL_FS_ERROR, path=user_path, error=log_key)
            return ToolExecutionResult(content=msg, is_error=True)

        if count == 0:
            snippet = content[:500]
            logger.info(
                TOOL_FS_EDIT_NOT_FOUND,
                path=user_path,
                old_text_preview=old_text[:100],
            )
            return ToolExecutionResult(
                content=(
                    f"Text not found in {user_path}. File content preview:\n{snippet}"
                ),
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
