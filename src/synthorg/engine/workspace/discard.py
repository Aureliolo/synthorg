# module-kind: code
"""Removing the workspace tree a deleted project leaves behind.

The ``project_workspaces`` row carries ``ON DELETE CASCADE``, so the database
forgets a deleted project's workspace immediately. Disk does not: a live run
finished with 24 trees under the workspace root, two belonging to projects an
operator had deleted through the dashboard during the same run. They are not
merely wasted space. Planning recall spans every project the org has run, and a
tree that outlives its project stays available as evidence for a plan that
should never have seen it.

Only the MANAGED directory goes. ``base_root/projects/<project_id>`` is ours by
construction: the layout is ours, the id is system-generated, and nothing else
can legitimately own that path. A workspace row may name somewhere else
entirely (a BYO ``LOCAL_PATH`` tree is the operator's own directory, which is
why the backend-switch path clears only ``.git`` there), and that path is never
touched from here.
"""

import asyncio
import shutil
import stat
from collections.abc import Callable
from pathlib import Path

from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.paths import project_workspace_dir
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import PROJECT_WORKSPACE_DISCARDED

logger = get_logger(__name__)


def force_writable_then_retry(
    func: Callable[[str], object],
    path: str,
    exc: BaseException,
) -> None:
    """``shutil.rmtree`` ``onexc`` handler: strip read-only, retry once.

    Git pack-object files under ``.git/objects`` are written read-only on
    Windows. ``shutil.rmtree`` raises ``PermissionError`` rather than stripping
    the attribute, which would leave an orphan tree behind.

    The chmod ORs the write bit into the existing mode (rather than replacing
    it) so read + execute bits are preserved; without them a directory entry
    would lose traversability mid-walk. ``lstat`` + ``follow_symlinks=False``
    keep the operation pinned to the named entry instead of any symlink target
    a third-party repo planted.

    Args:
        func: The failed removal call, retried once.
        path: The entry it failed on.
        exc: Why it failed.

    Raises:
        BaseException: *exc* unchanged, when the failure is not one a write bit
            explains or when the mode could not be changed.
    """
    if not isinstance(exc, PermissionError):
        raise exc
    try:
        current_mode = Path(path).lstat().st_mode
        Path(path).chmod(current_mode | stat.S_IWRITE, follow_symlinks=False)
    except OSError:
        raise exc from None
    func(path)


async def discard_project_workspace(
    *,
    base_root: Path,
    project_id: NotBlankStr,
) -> bool:
    """Remove *project_id*'s managed workspace tree.

    Idempotent, so re-issuing a delete whose first attempt failed part-way
    through is a no-op rather than an error.

    Args:
        base_root: Root every project's workspace lives under.
        project_id: The project whose tree is being discarded.

    Returns:
        Whether a tree was removed. ``False`` covers both "the project never
        provisioned one" and "the path holds something that is not a workspace".

    Raises:
        WorkspaceSetupError: *project_id* carries a path separator, so the
            resolved directory would sit outside the projects root.
        OSError: The tree exists and could not be removed. Surfaced rather than
            swallowed: an operator told a project was deleted while its files
            remain has been told something untrue.
    """
    tree = project_workspace_dir(base_root, project_id)
    if not await asyncio.to_thread(tree.is_dir):
        logger.info(
            PROJECT_WORKSPACE_DISCARDED,
            project_id=project_id,
            workspace_path=str(tree),
            removed=False,
        )
        return False
    await asyncio.to_thread(shutil.rmtree, tree, onexc=force_writable_then_retry)
    logger.info(
        PROJECT_WORKSPACE_DISCARDED,
        project_id=project_id,
        workspace_path=str(tree),
        removed=True,
    )
    return True


__all__ = ["discard_project_workspace", "force_writable_then_retry"]
