"""Workspace path validation for file system tools.

All file system tools delegate path validation to ``PathValidator``,
which ensures that resolved paths remain within the configured
workspace root.  This prevents path-traversal attacks via ``../``,
symlinks, or absolute paths outside the workspace.
"""

from pathlib import PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.tool import (
    TOOL_FS_PARENT_NOT_FOUND,
    TOOL_FS_PATH_VIOLATION,
    TOOL_FS_WORKSPACE_INVALID,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)


class PathValidator:
    """Validates and resolves paths against a workspace root.

    All resolved paths must remain within ``workspace_root``.
    Symlinks are resolved before checking, so a symlink pointing
    outside the workspace is rejected.

    Attributes:
        workspace_root: The resolved workspace root directory.
    """

    def __init__(self, workspace_root: Path) -> None:
        """Initialize with a workspace root directory.

        Args:
            workspace_root: Root directory that bounds all file access.

        Raises:
            ValueError: If workspace_root does not exist or is not a
                directory.
        """
        resolved = workspace_root.resolve()
        if not resolved.is_dir():
            logger.warning(
                TOOL_FS_WORKSPACE_INVALID,
                workspace_root=str(workspace_root),
            )
            msg = f"Workspace root is not an existing directory: {workspace_root}"
            raise ValueError(msg)
        self._workspace_root = resolved

    @property
    def workspace_root(self) -> Path:
        """The resolved workspace root directory."""
        return self._workspace_root

    def validate(self, path: str) -> Path:
        """Resolve *path* against workspace root and validate containment.

        Args:
            path: A relative or absolute path string from the user.

        Returns:
            The resolved ``Path`` guaranteed to be within the workspace.

        Raises:
            ValueError: If the resolved path escapes the workspace.
        """
        # Reject absolute paths outright — agents must use relative paths.
        if PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute():
            logger.warning(TOOL_FS_PATH_VIOLATION, user_path=path)
            msg = f"Absolute paths not allowed: {path}"
            raise ValueError(msg)

        # NOTE: There is an inherent TOCTOU gap between this validation
        # and the actual file operation (which runs in asyncio.to_thread).
        # A concurrent process could swap in a symlink between validation
        # and use.  Full mitigation requires OS-level sandboxing (e.g.
        # openat2 RESOLVE_BENEATH on Linux).  User-space path validation
        # is a best-effort defence-in-depth layer.
        try:
            resolved = (self._workspace_root / path).resolve()
        except OSError as exc:
            logger.warning(
                TOOL_FS_PATH_VIOLATION,
                user_path=path,
                error=str(exc),
            )
            msg = f"Invalid path: {path} ({exc})"
            raise ValueError(msg) from exc

        if not resolved.is_relative_to(self._workspace_root):
            logger.warning(
                TOOL_FS_PATH_VIOLATION,
                user_path=path,
            )
            msg = f"Path escapes workspace: {path}"
            raise ValueError(msg)
        return resolved

    def validate_parent_exists(self, path: str) -> Path:
        """Like ``validate`` but also checks that the parent directory exists.

        Args:
            path: A relative or absolute path string from the user.

        Returns:
            The resolved ``Path`` within the workspace whose parent exists.

        Raises:
            ValueError: If the resolved path escapes the workspace or
                the parent directory does not exist.
        """
        resolved = self.validate(path)
        if not resolved.parent.exists():
            logger.warning(
                TOOL_FS_PARENT_NOT_FOUND,
                path=path,
            )
            msg = f"Parent directory does not exist: {path}"
            raise ValueError(msg)
        return resolved
