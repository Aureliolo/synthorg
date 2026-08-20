# module-kind: code
"""Per-run workspace provisioning.

Each cell runs against a directory recreated from a committed seed fixture.
Recreating rather than reusing is the fair-comparison invariant every recorded
artifact rests on: if one run could inherit another's output, the grade would
measure run order instead of the thing under test.

The seed lands in a project subtree rather than at the cell root. A run is
attributed to :data:`~evals.runner.execution.EVAL_TASK_PROJECT`, and every
sandbox a cell drives (a native shell tool's, or a harness container's) picks
its mount by resolving that project id under the sandbox root, so a flat layout
is one neither can bind.

Both ``cell_key`` and ``seed_dir`` arrive from outside this module, so every
path built from them is resolved and re-checked against its root before any
filesystem work happens.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from evals.errors import WorkspacePathEscapeError, WorkspaceSeedNotFoundError
from evals.runner.execution import EVAL_TASK_PROJECT
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_HARNESS_WORKSPACE_PATH_ESCAPED,
    EVALS_WORKSPACE_SEEDED,
)

logger = get_logger(__name__)

#: Subdirectory the sandbox backends resolve a project id under.
_PROJECTS_SUBDIR: Final[str] = "projects"


@dataclass(frozen=True)
class CellWorkspace:
    """The two directories one cell needs, which are not the same directory.

    ``project_dir`` is derived rather than stored, because the two must name the
    same tree by construction. A pair that disagreed would send the loop's file
    tools to one directory and its shell (which re-derives the mount from
    ``root`` by project id) to another, and the brief would then be graded
    against whichever one the checks happened to read: wrong, silently, with no
    failure anywhere.

    Attributes:
        root: What a sandbox is bound to. The mount is selected beneath it by
            project id, so this is the parent of the graded tree, not the tree.
    """

    root: Path

    @property
    def project_dir(self) -> Path:
        """What the loop actually works in and is graded on.

        Returns:
            The project subtree the sandbox backends resolve under ``root``.
        """
        return self.root / _PROJECTS_SUBDIR / EVAL_TASK_PROJECT


def _contained(candidate: Path, root: Path) -> Path:
    """Resolve *candidate* and require it to stay inside *root*.

    Returns:
        The resolved path.

    Raises:
        WorkspacePathEscapeError: The resolved path lies outside *root*.
    """
    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()
    if not resolved.is_relative_to(resolved_root):
        # Logged before it is raised: the recorder removes and re-copies whole
        # trees under this root, so a path that got out of it is about
        # something on disk an operator will want to look at, and the raise
        # alone reaches only whatever caught it.
        logger.warning(
            EVALS_HARNESS_WORKSPACE_PATH_ESCAPED,
            candidate=str(candidate),
            root=str(resolved_root),
        )
        msg = f"path {str(candidate)!r} escapes the root {str(resolved_root)!r}"
        raise WorkspacePathEscapeError(msg)
    return resolved


def seed_workspace(
    *,
    cell_key: str,
    seed_dir: str,
    suite_root: Path,
    work_root: Path,
) -> CellWorkspace:
    """Recreate one cell's workspace from a committed seed fixture.

    The whole cell root is removed first, not just the project subtree, so a
    repeated call yields a workspace byte-identical to the committed fixture
    regardless of what a previous run left anywhere under the mount.

    Args:
        cell_key: Names the cell's tree under *work_root*. Reaches this from
            authored YAML or from a plan an agent wrote, so it is resolved and
            re-checked against its root like any other untrusted segment.
        seed_dir: The fixture to copy, relative to *suite_root*.
        suite_root: Directory *seed_dir* is resolved against.
        work_root: Directory per-cell roots are created under.

    Returns:
        The provisioned :class:`CellWorkspace`.

    Raises:
        WorkspaceSeedNotFoundError: The seed fixture directory does not exist.
        WorkspacePathEscapeError: A resolved path escapes its root.
    """
    seed = _contained(Path(seed_dir), suite_root)
    if not seed.is_dir():
        msg = (
            f"cell {cell_key!r} seed fixture {seed_dir!r} is not a directory "
            f"under {suite_root}; record it before recording anything"
        )
        raise WorkspaceSeedNotFoundError(msg)

    work_root.mkdir(parents=True, exist_ok=True)
    root = _contained(Path(cell_key), work_root)
    if root.exists():
        shutil.rmtree(root)
    workspace = CellWorkspace(root=root)
    # Re-checked after resolution even though the segments below ``root`` are
    # our own constants: ``cell_key`` reached ``root`` from outside, and a
    # symlink planted in a previous run's tree could redirect the copy.
    project_dir = _contained(Path(_PROJECTS_SUBDIR) / EVAL_TASK_PROJECT, root)
    shutil.copytree(seed, project_dir)

    logger.info(
        EVALS_WORKSPACE_SEEDED,
        cell_key=cell_key,
        seed_dir=seed_dir,
        project=EVAL_TASK_PROJECT,
    )
    return workspace


__all__ = ["CellWorkspace", "seed_workspace"]
