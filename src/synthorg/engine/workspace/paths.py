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
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    WORKSPACE_PATH_TRAVERSAL_REJECTED,
)

logger = get_logger(__name__)

#: Subdirectory of the workspace root holding one directory per project.
PROJECTS_SUBDIR: Final[str] = "projects"

#: Characters that would make the id something other than one directory name.
#: ``:`` is here because a segment carrying a drive resets the whole join on
#: Windows, which a separator check alone does not see.
_FORBIDDEN_CHARS: Final[tuple[str, ...]] = ("/", "\\", "..", ":")

#: Names pathlib resolves to somewhere other than a child of the projects root.
_DOT_NAMES: Final[frozenset[str]] = frozenset({".", ".."})


def project_workspace_dir(base_root: Path, project_id: str) -> Path:
    """Resolve *project_id*'s workspace directory under *base_root*.

    A ``project_id`` is system-generated and reaches this seam only through
    persisted rows, but a component that is not a plain name would escape the
    projects subdirectory or take over the base root outright, so it is refused
    here rather than trusted at every call site. This guard is the only thing
    standing between the deletion path and the root holding every project's
    tree, so it refuses more than separators: pathlib drops a lone ``.``
    component during parsing, which would resolve to the projects root itself,
    and a segment carrying a drive resets the join entirely.

    Args:
        base_root: Root the workspaces of every project live under.
        project_id: The project whose directory to resolve.

    Returns:
        The project's workspace directory (which may not exist yet).

    Raises:
        WorkspaceSetupError: When *project_id* is anything other than a plain
            directory name under the projects root.
    """
    if any(char in project_id for char in _FORBIDDEN_CHARS) or project_id in _DOT_NAMES:
        msg = (
            f"refusing non-name project_id {project_id!r}: "
            "workspace path traversal blocked"
        )
        logger.warning(
            WORKSPACE_PATH_TRAVERSAL_REJECTED,
            project_id=project_id,
            reason="not_a_plain_name",
        )
        raise WorkspaceSetupError(msg)
    resolved = base_root / PROJECTS_SUBDIR / project_id
    projects_root = base_root / PROJECTS_SUBDIR
    # Containment is judged on the resolved pair and nothing else is: the
    # answer must be about where the path physically lands, or a symlink named
    # like a project id passes a purely lexical check. Both sides resolve
    # together, since resolving one alone would fail a legitimately symlinked
    # base against its own root. The UNRESOLVED join is what gets returned,
    # because callers mount and compare the configured path, and canonicalising
    # it here would hand them a different one than they asked for.
    if (
        resolved.resolve() == projects_root.resolve()
        or projects_root.resolve() not in resolved.resolve().parents
    ):
        # Belt to the character guard's braces: the guard reasons about the
        # id's spelling, this reasons about where the join actually landed, and
        # only the second survives a symlink or a future pathlib normalisation
        # nobody here anticipated.
        msg = (
            f"refusing project_id {project_id!r}: resolved path "
            f"{resolved} is not a directory under {projects_root}"
        )
        logger.warning(
            WORKSPACE_PATH_TRAVERSAL_REJECTED,
            project_id=project_id,
            reason="escaped_projects_root",
        )
        raise WorkspaceSetupError(msg)
    return resolved


__all__ = ["PROJECTS_SUBDIR", "project_workspace_dir"]
