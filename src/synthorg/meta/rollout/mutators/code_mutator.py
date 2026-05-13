"""CodeMutator implementation backed by the workspace filesystem.

Restores a source file's contents from a rollback operation. Writes
go through ``PathValidator`` so paths cannot escape the configured
workspace root, and the actual file write is atomic (write to a temp
file alongside the target, then ``os.replace``) so a crash mid-write
cannot leave a half-written source file.
"""

import asyncio
import contextlib
import os
import tempfile
from pathlib import Path

from synthorg.meta.errors import RollbackMutationDeniedError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import META_ROLLBACK_OPERATION_FAILED
from synthorg.tools.file_system._path_validator import PathValidator

logger = get_logger(__name__)


def _atomic_write(target: Path, content: str) -> None:
    """Write ``content`` to ``target`` atomically.

    Strategy: write to a temp file in the same directory (so the
    rename stays on one filesystem), fsync, then ``os.replace``. The
    rename is atomic on POSIX and Windows for files on the same
    volume, so a crash in the middle leaves either the old file or
    the new file fully written, never a half-written one.
    """
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, temp_str = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".rollback",
        dir=parent,
    )
    temp_path = Path(temp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        temp_path.replace(target)
    except BaseException:
        # Clean up the temp file on any failure so the workspace
        # does not accumulate ``.<name>.<rand>.rollback`` orphans.
        with contextlib.suppress(OSError):
            temp_path.unlink()
        raise


class WorkspaceCodeMutator:
    """Concrete ``CodeMutator`` that writes inside a workspace root.

    Uses :class:`PathValidator` so any attempt to escape the workspace
    (``../`` traversal, absolute paths, symlinks pointing outside) is
    rejected with a structured ``META_ROLLBACK_OPERATION_FAILED``
    log and a :class:`RollbackMutationDeniedError`.
    """

    def __init__(self, *, workspace_root: Path) -> None:
        self._path_validator = PathValidator(workspace_root)

    async def revert_file(self, *, path: str, content: str) -> None:
        """Restore ``path`` to ``content`` atomically.

        Args:
            path: Workspace-relative file path. Validated against
                ``workspace_root``; any escape attempt raises
                :class:`RollbackMutationDeniedError`.
            content: Full file contents to write (UTF-8 encoded on disk).

        Raises:
            RollbackMutationDeniedError: If the path escapes the
                workspace, or the underlying write fails.
        """
        try:
            resolved = self._path_validator.validate(path)
        except ValueError as exc:
            logger.warning(
                META_ROLLBACK_OPERATION_FAILED,
                operation_type="revert_code",
                target=path,
                reason="path_validation_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"revert_code rejected: invalid workspace path {path!r}"
            raise RollbackMutationDeniedError(msg) from exc
        try:
            await asyncio.to_thread(_atomic_write, resolved, content)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                META_ROLLBACK_OPERATION_FAILED,
                operation_type="revert_code",
                target=path,
                reason="write_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"revert_code rejected: write failed for {path!r}"
            raise RollbackMutationDeniedError(msg) from exc
