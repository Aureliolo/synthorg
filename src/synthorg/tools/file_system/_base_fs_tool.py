"""Base class for file system tools.

Provides the common ``ToolCategory.FILE_SYSTEM`` category and a
``PathValidator`` scoped to the workspace of the project being worked on.

The scope is resolved per call from the bound execution identity, not fixed
at construction, because the registry is built once per boot and shared by
every agent and every project. A single root would put two projects' files
in one directory, and it would put them somewhere neither the sandbox (which
runs in ``<base>/projects/<project_id>``) nor the artifact check looks, so a
delivered file would read as never produced.
"""

from abc import ABC
from pathlib import Path

from pydantic import JsonValue

from synthorg.core.execution_identity import current_execution_identity
from synthorg.engine.workspace.paths import project_workspace_dir
from synthorg.observability import safe_error_description
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool
from synthorg.tools.file_system._path_validator import PathValidator


def _map_os_error(
    exc: OSError,
    user_path: str,
    verb: str,
    *,
    dedicated_tool_hint: str | None = None,
) -> tuple[str, str]:
    """Map an OS error to ``(log_key, user_message)`` for FS operations.

    Args:
        exc: The caught OS-level exception.
        user_path: The original user-supplied path string.
        verb: Action verb for the fallback message
            (e.g. ``"reading"``, ``"editing"``).
        dedicated_tool_hint: When supplied and ``exc`` is an
            ``IsADirectoryError``, the hint is appended to the message
            in parentheses so callers can point the user at a
            verb-specific alternative tool (e.g. directory deletion).

    Returns:
        A two-tuple of (structured log key, human-readable message).
    """
    if isinstance(exc, FileNotFoundError):
        return "not_found", f"File not found: {user_path}"
    if isinstance(exc, IsADirectoryError):
        msg = f"Path is a directory, not a file: {user_path}"
        if dedicated_tool_hint is not None:
            msg = f"{msg} ({dedicated_tool_hint})"
        return "is_directory", msg
    if isinstance(exc, PermissionError):
        return "permission_denied", f"Permission denied: {user_path}"
    return (
        "os_error",
        f"OS error {verb} file '{user_path}': {safe_error_description(exc)}",
    )


class BaseFileSystemTool(BaseTool, ABC):
    """Abstract base for all file system tools.

    Sets ``category=ToolCategory.FILE_SYSTEM`` and holds a shared
    ``PathValidator`` for workspace-scoped path resolution.
    """

    def __init__(
        self,
        *,
        workspace_root: Path,
        name: str,
        description: str = "",
        parameters_schema: dict[str, JsonValue] | None = None,
        action_type: str | None = None,
    ) -> None:
        """Initialize with a workspace root and tool metadata.

        Args:
            workspace_root: Root directory bounding file access.
            name: Tool name.
            description: Human-readable description.
            parameters_schema: JSON Schema for tool parameters.
            action_type: Security action type override.
        """
        super().__init__(
            name=name,
            description=description,
            category=ToolCategory.FILE_SYSTEM,
            parameters_schema=parameters_schema,
            action_type=action_type,
        )
        self._base_root = workspace_root
        self._base_validator = PathValidator(workspace_root)
        self._scoped: dict[str, PathValidator] = {}

    @property
    def workspace_root(self) -> Path:
        """The directory bounding file access for the current execution."""
        return self.path_validator.workspace_root

    @property
    def path_validator(self) -> PathValidator:
        """The path validator for the project this execution belongs to.

        Falls back to the shared base root outside a bound execution scope
        (a tool exercised directly, or a run with no project), which is the
        only case where there is no project to scope to.

        Returns:
            A validator rooted at the current project's workspace.
        """
        identity = current_execution_identity()
        project_id = identity.project_id if identity is not None else None
        if project_id is None:
            return self._base_validator
        cached = self._scoped.get(project_id)
        if cached is not None:
            return cached
        root = project_workspace_dir(self._base_root, project_id)
        # Created on resolve, matching how the base root itself is resolved:
        # PathValidator refuses a missing directory, and a project whose
        # workspace was never provisioned would otherwise fail every file
        # call rather than starting empty.
        root.mkdir(parents=True, exist_ok=True)
        validator = PathValidator(root)
        self._scoped[project_id] = validator
        return validator
