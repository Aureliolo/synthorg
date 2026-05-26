"""Workspace-scoped desktop screenshot store.

Resolves screenshot PNG paths under
``<workspace>/.synthorg/desktop/screenshots/``. The persistence
boundary is filesystem-only; no sqlite / psycopg involvement. The
vision verifier reads the saved PNGs by the relative path the tool
reports.
"""

from pathlib import Path  # noqa: TC003 -- runtime use in workspace resolution
from typing import Final

from synthorg.tools.desktop._constants import PNG_EXTENSION, SCREENSHOTS_SUBDIR
from synthorg.tools.desktop.errors import DesktopDomainError

_FORBIDDEN_SEGMENTS: Final[frozenset[str]] = frozenset({"..", "."})


class WorkspaceScreenshotStore:
    """Resolves screenshot paths rooted at the persistent workspace."""

    def __init__(self, *, workspace: Path) -> None:
        """Initialise the store rooted at the persistent workspace.

        Raises:
            DesktopDomainError: If the related operation fails.
        """
        if not workspace.is_absolute():
            msg = f"workspace must be absolute, got {workspace!r}"
            raise DesktopDomainError(msg)
        self._workspace = workspace.resolve()
        self._root = self._workspace / SCREENSHOTS_SUBDIR

    @property
    def workspace(self) -> Path:
        """Absolute workspace root."""
        return self._workspace

    @property
    def root(self) -> Path:
        """Absolute screenshots directory."""
        return self._root

    def screenshot_path(self, *, screenshot_name: str) -> Path:
        """Return the canonical PNG path for a named screenshot.

        Returns:
            Result of type ``Path``.
        """
        self._reject_traversal(screenshot_name)
        return self._root / f"{screenshot_name}{PNG_EXTENSION}"

    def relative(self, absolute_path: Path) -> str:
        """Return a path expressed relative to the workspace root.

        Returns:
            Result of type ``str``.
        """
        return absolute_path.resolve().relative_to(self._workspace).as_posix()

    @staticmethod
    def _reject_traversal(name: str) -> None:
        """Reject names containing ``..`` segments or path separators.

        Raises:
            DesktopDomainError: If the related operation fails.
        """
        if not name:
            raise DesktopDomainError("screenshot name must be non-empty")
        if "/" in name or "\\" in name:
            raise DesktopDomainError(
                "screenshot name must not contain path separators",
                context={"name": name},
            )
        if name in _FORBIDDEN_SEGMENTS:
            raise DesktopDomainError(
                "screenshot name must not be '.' or '..'",
                context={"name": name},
            )
