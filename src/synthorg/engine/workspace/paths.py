# module-kind: code
"""Where a project's workspace lives under the shared workspace root.

One definition of the layout, because several subsystems resolve the same
directory independently: the workspace service that provisions it, the
worktree strategy that branches inside it, and the artifact check that asks
whether a declared deliverable is actually there. A second copy of the
layout is a silent divergence: each caller would keep working while looking
at a different directory.
"""

from pathlib import Path
from typing import Final

from synthorg.engine.errors import WorkspaceSetupError

#: Subdirectory of the workspace root holding one directory per project.
PROJECTS_SUBDIR: Final[str] = "projects"


def project_workspace_dir(base_root: Path, project_id: str) -> Path:
    """Resolve *project_id*'s workspace directory under *base_root*.

    A ``project_id`` is system-generated and reaches this seam only through
    persisted rows, but a path separator in one would escape the projects
    subdirectory or take over the base root outright, so it is refused here
    rather than trusted at every call site.

    Args:
        base_root: Root the workspaces of every project live under.
        project_id: The project whose directory to resolve.

    Returns:
        The project's workspace directory (which may not exist yet).

    Raises:
        WorkspaceSetupError: When *project_id* carries a path separator or a
            parent-directory reference.
    """
    if "/" in project_id or "\\" in project_id or ".." in project_id:
        msg = (
            f"refusing path-separator-bearing project_id "
            f"{project_id!r}: workspace path traversal blocked"
        )
        raise WorkspaceSetupError(msg)
    return base_root / PROJECTS_SUBDIR / project_id


__all__ = ["PROJECTS_SUBDIR", "project_workspace_dir"]
