"""Path-safety + asset-staging helpers for the browser tool.

Extracted from :mod:`synthorg.tools.browser.browser_tool` so the tool
module stays within its size budget. Both helpers are self-contained:
workspace-relative path validation and a stale-copy guard for the
deployed executor / axe bundles.
"""

import shutil
from pathlib import Path

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.browser import (
    BROWSER_ARGS_VALIDATION_FAILED,
    BROWSER_ASSET_DEPLOY_FAILED,
)
from synthorg.tools.browser.errors import BrowserArgumentError, BrowserLaunchError

logger = get_logger(__name__)


def reject_path_traversal(path: str) -> None:
    """Reject ``..`` segments and absolute paths in a workspace-relative path.

    Raises:
        BrowserArgumentError: If the path is absolute or contains a ``..``
            segment.
    """
    if path.startswith("/"):
        logger.warning(
            BROWSER_ARGS_VALIDATION_FAILED,
            reason="absolute_path_rejected",
            error_type=BrowserArgumentError.__name__,
        )
        raise BrowserArgumentError(
            "path must be workspace-relative, not absolute",
            context={"path": path},
        )
    if any(segment == ".." for segment in path.split("/")):
        logger.warning(
            BROWSER_ARGS_VALIDATION_FAILED,
            reason="path_traversal_rejected",
            error_type=BrowserArgumentError.__name__,
        )
        raise BrowserArgumentError(
            "path must not contain '..' segments",
            context={"path": path},
        )


def copy_if_stale(source: Path, target: Path) -> bool:
    """Copy *source* to *target* when the target is missing or older.

    Returns:
        ``True`` if the file was copied, ``False`` if the target was
        already up to date.

    Raises:
        BrowserLaunchError: If a filesystem error prevents staging the
            asset (surfaced with deploy context instead of an opaque
            ``OSError`` traceback with no log).
    """
    try:
        stale = not target.exists() or (target.stat().st_mtime < source.stat().st_mtime)
        if stale:
            shutil.copyfile(source, target)
    except OSError as exc:
        logger.error(
            BROWSER_ASSET_DEPLOY_FAILED,
            source=str(source),
            target=str(target),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = "failed to stage browser asset"
        raise BrowserLaunchError(
            msg,
            context={"source": str(source), "target": str(target)},
        ) from exc
    else:
        return stale
