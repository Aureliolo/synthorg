"""Filesystem primitives for the code applier.

Single-change write / revert with precondition checks, plus the
path-containment guard. Each primitive validates filesystem state
before mutating so a file that drifted since proposal generation is
never clobbered.
"""

from pathlib import Path

from synthorg.meta.models import CodeChange, CodeOperation
from synthorg.observability import get_logger
from synthorg.observability.events.meta import (
    META_APPLY_CREATE_TARGET_EXISTS,
    META_APPLY_DELETE_CONTENT_DRIFT,
    META_APPLY_DELETE_TARGET_MISSING,
    META_APPLY_MODIFY_CONTENT_DRIFT,
    META_APPLY_MODIFY_TARGET_MISSING,
)

logger = get_logger(__name__)


def _apply_single_change(change: CodeChange, file_path: Path) -> None:
    """Write a single code change to disk with precondition checks.

    Validates filesystem state before mutating to avoid clobbering
    files that changed since proposal generation.

    Args:
        change: The code change descriptor.
        file_path: Absolute path to write.

    Raises:
        RuntimeError: If preconditions are violated.
    """
    if change.operation == CodeOperation.CREATE:
        if file_path.exists():
            logger.error(
                META_APPLY_CREATE_TARGET_EXISTS,
                file_path=change.file_path,
            )
            msg = f"CREATE target already exists: {change.file_path}"
            raise RuntimeError(msg)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(change.new_content, encoding="utf-8")
    elif change.operation == CodeOperation.MODIFY:
        if not file_path.exists():
            logger.error(
                META_APPLY_MODIFY_TARGET_MISSING,
                file_path=change.file_path,
            )
            msg = f"MODIFY target does not exist: {change.file_path}"
            raise RuntimeError(msg)
        current = file_path.read_text(encoding="utf-8")
        if current != change.old_content:
            logger.error(
                META_APPLY_MODIFY_CONTENT_DRIFT,
                file_path=change.file_path,
            )
            msg = f"MODIFY target changed since proposal generation: {change.file_path}"
            raise RuntimeError(msg)
        file_path.write_text(change.new_content, encoding="utf-8")
    elif change.operation == CodeOperation.DELETE:
        if not file_path.exists():
            logger.error(
                META_APPLY_DELETE_TARGET_MISSING,
                file_path=change.file_path,
            )
            msg = f"DELETE target does not exist: {change.file_path}"
            raise RuntimeError(msg)
        current = file_path.read_text(encoding="utf-8")
        if current != change.old_content:
            logger.error(
                META_APPLY_DELETE_CONTENT_DRIFT,
                file_path=change.file_path,
            )
            msg = f"DELETE target changed since proposal generation: {change.file_path}"
            raise RuntimeError(msg)
        file_path.unlink()


def _is_within(candidate: Path, root: Path) -> bool:
    """Check that candidate resolves to a path inside root.

    Args:
        candidate: Path to validate.
        root: Already-resolved project root.

    Returns:
        True if candidate is a descendant of root.
    """
    try:
        candidate.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _revert_single_change(
    change: CodeChange,
    path: Path,
    *,
    defensive: bool,
) -> None:
    """Revert a single local file change.

    Args:
        change: The code change to undo.
        path: Absolute file path.
        defensive: Skip revert if file state doesn't indicate the
            change was applied (prevents overwriting untouched files).
    """
    if change.operation == CodeOperation.CREATE:
        if defensive:
            # Only delete if the file's current contents match what
            # ``_apply_single_change`` would have written. Without this
            # check, defensive revert of a CREATE proposal whose target
            # accidentally pre-existed (caused the precondition failure
            # in ``_apply_single_change``) would clobber the operator's
            # pre-existing file.
            if not path.exists():
                return
            current = path.read_text(encoding="utf-8")
            if current != change.new_content:
                return
        path.unlink(missing_ok=True)
    elif change.operation == CodeOperation.MODIFY:
        if defensive:
            if not path.exists():
                return
            # Only revert if content matches new_content (was applied).
            current = path.read_text(encoding="utf-8")
            if current != change.new_content:
                return
        path.write_text(change.old_content, encoding="utf-8")
    elif change.operation == CodeOperation.DELETE:
        if defensive and path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(change.old_content, encoding="utf-8")
