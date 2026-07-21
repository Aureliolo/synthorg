"""Edit file tool -- search-and-replace within workspace files.

Supports a single search-and-replace or an ordered batch of hunks applied
atomically (all-or-nothing) to one file. A hunk whose ``old_text`` matches
more than once is rejected unless ``replace_all`` is set, so an agent never
silently edits the wrong occurrence.
"""

import asyncio
import os
import pathlib
import tempfile
from pathlib import Path
from typing import ClassVar, Final, NamedTuple, override

from pydantic import BaseModel

from synthorg.core.boundary import parse_typed
from synthorg.observability import get_logger
from synthorg.observability.events.tool import (
    TOOL_FS_EDIT,
    TOOL_FS_EDIT_NOT_FOUND,
    TOOL_FS_ERROR,
    TOOL_FS_NOOP,
    TOOL_FS_SIZE_EXCEEDED,
)
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.file_system._args import EditFileArgs, EditHunk
from synthorg.tools.file_system._base_fs_tool import (
    BaseFileSystemTool,
    _map_os_error,
)

logger = get_logger(__name__)

MAX_EDIT_FILE_SIZE_BYTES: Final[int] = 1_048_576  # 1 MB

_ERROR_NOT_FOUND: Final[str] = "not_found"
_ERROR_NOT_UNIQUE: Final[str] = "not_unique"


class _EditPlan(NamedTuple):
    """Outcome of planning an edit without writing.

    ``resulting`` equals ``original`` whenever nothing was (or could be)
    changed, so the write step is skipped for a no-op or a rejected plan.
    ``edits_applied`` counts only the hunks that actually changed the file
    (no-op hunks whose ``old_text`` equals ``new_text`` are skipped), so the
    applied-edit report never over-counts. On rejection ``error_kind`` names
    the failure and ``error_hunk_index`` /``error_count`` locate it for the
    operator-facing message.
    """

    original: str
    resulting: str
    occurrences_found: int
    occurrences_replaced: int
    error_kind: str | None = None
    error_hunk_index: int | None = None
    error_count: int = 0
    edits_applied: int = 0


def _plan_edits_sync(resolved: Path, hunks: tuple[EditHunk, ...]) -> _EditPlan:
    """Read the file and compute the post-edit content for a hunk sequence.

    Hunks are applied in order to an in-memory copy, so a later hunk sees the
    result of the earlier ones. A hunk whose ``old_text`` is absent, or is
    non-unique without ``replace_all``, rejects the whole plan (``resulting``
    is returned equal to ``original`` so nothing is written). A hunk whose
    ``old_text`` equals its ``new_text`` is a no-op and is skipped without a
    uniqueness check.

    Args:
        resolved: Resolved file path within the workspace.
        hunks: Ordered edit hunks to apply atomically.

    Returns:
        An :class:`_EditPlan` carrying the original and resulting content,
        occurrence counts, and (on rejection) the failure descriptor.

    Raises:
        UnicodeDecodeError: If the file contains non-UTF-8 bytes.
        FileNotFoundError: If the file does not exist.
        PermissionError: If the process lacks read/write permission.
        OSError: For other OS-level I/O failures.
    """
    original = resolved.read_text(encoding="utf-8")
    working = original
    found_total = 0
    replaced_total = 0
    applied_total = 0
    for index, hunk in enumerate(hunks):
        if hunk.old_text == hunk.new_text:
            continue
        count = working.count(hunk.old_text)
        if count == 0:
            return _EditPlan(original, original, 0, 0, _ERROR_NOT_FOUND, index)
        if count > 1 and not hunk.replace_all:
            return _EditPlan(original, original, 0, 0, _ERROR_NOT_UNIQUE, index, count)
        found_total += count
        applied_total += 1
        if hunk.replace_all:
            working = working.replace(hunk.old_text, hunk.new_text)
            replaced_total += count
        else:
            working = working.replace(hunk.old_text, hunk.new_text, 1)
            replaced_total += 1
    return _EditPlan(
        original, working, found_total, replaced_total, edits_applied=applied_total
    )


def _write_sync(resolved: Path, new_content: str) -> None:
    """Write *new_content* atomically (temp file + replace).

    The atomic pattern ensures a crash or disk-full during the write does not
    corrupt the original file.

    Raises:
        OSError: For OS-level I/O failures.
        BaseException: Re-raised after unlinking the temp file on any failure.
    """
    fd, tmp_path = tempfile.mkstemp(dir=str(resolved.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new_content)
            fh.flush()
            os.fsync(fh.fileno())
        pathlib.Path(tmp_path).replace(resolved)
    except BaseException:
        pathlib.Path(tmp_path).unlink(missing_ok=True)
        raise


class EditFileTool(BaseFileSystemTool):
    """Search-and-replace within a workspace file, single or multi-hunk.

    Single-edit mode replaces ``old_text`` with ``new_text``; the match
    must be unique unless ``replace_all`` is set, otherwise the edit is
    rejected so the wrong occurrence is never silently changed. Batch mode
    applies an ordered ``edits`` list atomically (all-or-nothing): if any
    hunk cannot be applied, the file is left untouched.

    If ``old_text`` is not found the edit is rejected. Returns with no
    change when the resulting content equals the original.

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

        Atomic multi-hunk edit::

            result = await tool.execute(
                arguments={
                    "path": "main.py",
                    "edits": [
                        {"old_text": "foo", "new_text": "bar"},
                        {"old_text": "baz", "new_text": "qux", "replace_all": True},
                    ],
                }
            )
    """

    args_model: ClassVar[type[BaseModel] | None] = EditFileArgs

    def __init__(self, *, workspace_root: Path) -> None:
        """Initialize the edit-file tool, deriving its schema from EditFileArgs."""
        super().__init__(
            workspace_root=workspace_root,
            name="edit_file",
            action_type=ActionType.CODE_WRITE,
            description=(
                "Edit a file by replacing text. Single edit: pass old_text and "
                "new_text (empty new_text deletes the match); set replace_all "
                "to replace every occurrence, otherwise the match must be "
                "unique. Batch: pass edits=[{old_text,new_text,replace_all?}] "
                "applied in order, atomically."
            ),
            parameters_schema=EditFileArgs.model_json_schema(),
        )

    def _guard_output_policy(
        self,
        user_path: str,
        original: str,
        resulting: str,
    ) -> ToolExecutionResult | None:
        """Enforce the output-style policy on the post-edit file content.

        Evaluates the complete candidate content (existing file after all
        hunks of the edit are applied), not just the replacement fragment, so
        a violation formed at the boundary between an edit and its surroundings
        is caught. Only a violation the edit *introduces* blocks: an edit
        elsewhere in a file that already violated stays editable. Code-channel
        (reject, never auto-rewrite), unless an operator-sanctioned PATH
        exemption covers this file.

        Returns:
            An error result when the edit introduces a hard-rule violation, else
            ``None``.
        """
        from synthorg.engine.output_style import (  # noqa: PLC0415
            OutputChannel,
            OutputContext,
            evaluate_output_policy,
        )

        ctx = OutputContext(
            channel=OutputChannel.CODE_FILE,
            file_path=user_path or None,
        )
        after = evaluate_output_policy(resulting, ctx)
        if after is None or not after.blocked:
            return None
        before = evaluate_output_policy(original, ctx)
        if before is not None and before.blocked:
            return None
        return ToolExecutionResult(content=after.summary, is_error=True)

    async def _preflight_check_file(
        self,
        user_path: str,
        resolved: Path,
    ) -> ToolExecutionResult | None:
        """Verify the file is editable (exists, not a dir, not too large).

        Returns:
            The resulting ``ToolExecutionResult``, or ``None`` when unavailable.
        """
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

    def _reject_plan(
        self,
        user_path: str,
        plan: _EditPlan,
        hunk_count: int,
    ) -> ToolExecutionResult:
        """Build the error result for a rejected (unwritten) edit plan.

        Returns:
            A ``ToolExecutionResult`` with ``is_error=True`` describing the
            failing hunk; nothing is written for a rejected plan.
        """
        index = plan.error_hunk_index or 0
        prefix = f"Edit {index + 1} of {hunk_count}: " if hunk_count > 1 else ""
        suffix = " No changes applied." if hunk_count > 1 else ""
        if plan.error_kind == _ERROR_NOT_UNIQUE:
            logger.warning(
                TOOL_FS_EDIT_NOT_FOUND,
                path=user_path,
                reason="not_unique",
                occurrences=plan.error_count,
            )
            content = (
                f"{prefix}old_text is not unique in {user_path} "
                f"({plan.error_count} matches); make it unique or set "
                f"replace_all=true.{suffix}"
            )
        else:
            logger.warning(
                TOOL_FS_EDIT_NOT_FOUND,
                path=user_path,
                reason="not_found",
            )
            content = (
                f"Text not found in {user_path}.{suffix}"
                if hunk_count == 1
                else f"{prefix}text not found in {user_path}.{suffix}"
            )
        return ToolExecutionResult(
            content=content,
            is_error=True,
            metadata={
                "path": user_path,
                # ``error_count`` is the match count for the non-unique branch
                # (the reason for rejection) and 0 for not-found, so it is the
                # honest "how many were found" figure either way.
                "occurrences_found": plan.error_count,
                "occurrences_replaced": 0,
            },
        )

    async def _perform_edit(
        self,
        user_path: str,
        resolved: Path,
        hunks: tuple[EditHunk, ...],
    ) -> ToolExecutionResult:
        """Plan and apply the hunks atomically, returning the result.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        try:
            plan = await asyncio.to_thread(_plan_edits_sync, resolved, hunks)
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

        if plan.error_kind is not None:
            return self._reject_plan(user_path, plan, len(hunks))

        if plan.resulting == plan.original:
            return self._noop_result(user_path, plan)

        if guard_err := self._guard_output_policy(
            user_path, plan.original, plan.resulting
        ):
            return guard_err
        try:
            await asyncio.to_thread(_write_sync, resolved, plan.resulting)
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError) as exc:
            log_key, msg = _map_os_error(exc, user_path, "editing")
            logger.warning(TOOL_FS_ERROR, path=user_path, error=log_key)
            return ToolExecutionResult(content=msg, is_error=True)

        return self._success_result(user_path, plan, len(hunks))

    def _noop_result(self, user_path: str, plan: _EditPlan) -> ToolExecutionResult:
        """Build the no-change result when the plan leaves content untouched.

        Returns:
            A non-error ``ToolExecutionResult`` reporting no change.
        """
        logger.debug(TOOL_FS_NOOP, path=user_path, reason="content_unchanged")
        return ToolExecutionResult(
            content=f"No change needed in {user_path}: content unchanged",
            metadata={
                "path": user_path,
                "occurrences_found": plan.occurrences_found,
                "occurrences_replaced": 0,
            },
        )

    def _success_result(
        self, user_path: str, plan: _EditPlan, submitted_count: int
    ) -> ToolExecutionResult:
        """Build the applied-edit result after a successful write.

        ``submitted_count`` (the number of hunks the caller passed) only
        selects single- vs batch-mode phrasing; the reported edit count is
        ``plan.edits_applied``, which excludes no-op hunks so a batch mixing
        a no-op with a real hunk never claims more edits than it made.

        Returns:
            A non-error ``ToolExecutionResult`` summarising the applied edit.
        """
        replaced = plan.occurrences_replaced
        applied = plan.edits_applied
        if submitted_count > 1:
            content = (
                f"Applied {applied} {'edit' if applied == 1 else 'edits'} "
                f"({replaced} "
                f"{'occurrence' if replaced == 1 else 'occurrences'} replaced) "
                f"in {user_path}"
            )
        else:
            content = (
                f"Replaced {replaced} "
                f"{'occurrence' if replaced == 1 else 'occurrences'} in "
                f"{user_path}"
            )
        logger.info(
            TOOL_FS_EDIT,
            path=user_path,
            occurrences_found=plan.occurrences_found,
            occurrences_replaced=replaced,
            hunks=applied,
        )
        return ToolExecutionResult(
            content=content,
            metadata={
                "path": user_path,
                "occurrences_found": plan.occurrences_found,
                "occurrences_replaced": replaced,
                "edits_applied": applied,
            },
        )

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Edit a file by replacing text (single or atomic multi-hunk).

        Args:
            arguments: Must contain ``path`` plus either
                ``old_text``/``new_text`` (single edit) or ``edits``
                (batch).

        Returns:
            A ``ToolExecutionResult`` confirming the edit or an error.
        """
        args = parse_typed("tool.execute", arguments, EditFileArgs)
        user_path = args.path
        hunks = args.normalized_hunks()

        try:
            resolved = self.path_validator.validate(user_path)
        except ValueError as exc:
            return ToolExecutionResult(content=str(exc), is_error=True)

        if err := await self._preflight_check_file(user_path, resolved):
            return err

        return await self._perform_edit(user_path, resolved, hunks)
