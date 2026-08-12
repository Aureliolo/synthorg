"""Validation for the container paths a sandbox mounts.

Lives beside the config rather than inside it: the rules are about POSIX paths
and the workspace bind, not about the config model, and the config module sits
against its size cap.
"""

from pathlib import PurePosixPath
from typing import Final

from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED

logger = get_logger(__name__)

# The in-container mount point for the project workspace: the host project
# root is bind-mounted here and every container runs with it as its cwd.
# Shared by the sandbox backend and its streaming and sidecar mixins, so the
# mount path has a single source of truth.
CONTAINER_WORKSPACE: Final[str] = "/workspace"
# A path inside the container, never a directory on this host.
CONTAINER_TMP: Final[str] = "/tmp"  # noqa: S108


def _rejection(raw: str, seen: set[str]) -> str | None:
    """Return why *raw* is unusable as an extra tmpfs mount, or ``None``.

    Args:
        raw: The candidate container path.
        seen: Normalised paths already accepted in this declaration.

    Returns:
        The failure message, or ``None`` when the path is usable.
    """
    path = PurePosixPath(raw)
    workspace = PurePosixPath(CONTAINER_WORKSPACE)
    if not path.is_absolute() or path == PurePosixPath("/"):
        return f"extra_tmpfs_paths entries must be absolute, got: {raw!r}"
    if ".." in path.parts:
        # Rejected rather than collapsed: pathlib leaves ``..`` in place while
        # the kernel resolves it, so ``/tmp/../workspace/x`` reads as outside
        # the bind here and lands inside it there. A mount point that has to
        # be normalised to be understood is not one anybody meant to write.
        return (
            f"extra_tmpfs_paths entry {raw!r} must name its mount point "
            f"directly, without a '..' segment"
        )
    if path == workspace or workspace in path.parents:
        return (
            f"extra_tmpfs_paths entry {raw!r} would mount over the workspace "
            f"bind at {CONTAINER_WORKSPACE}"
        )
    # Keyed on the parsed path, not the raw string: ``/cache`` and ``/cache/``
    # are one mount point spelled two ways.
    if str(path) in seen:
        return f"duplicate extra_tmpfs_paths entry: {raw!r}"
    return None


def validate_extra_tmpfs_paths(paths: tuple[str, ...]) -> None:
    """Reject any extra tmpfs mount that is not an absolute path outside the bind.

    A tmpfs over the workspace would hide the bind mount, so everything the
    agent produced would be reclaimed with the container while the run still
    reported success.

    Args:
        paths: The declared container paths.

    Raises:
        ValueError: If an argument fails domain validation.
    """
    seen: set[str] = set()
    for raw in paths:
        msg = _rejection(raw, seen)
        if msg is None:
            seen.add(str(PurePosixPath(raw)))
            continue
        logger.warning(
            CONFIG_VALIDATION_FAILED,
            field="extra_tmpfs_paths",
            reason=msg,
        )
        raise ValueError(msg)
