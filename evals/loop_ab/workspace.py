# module-kind: code
"""Per-run workspace provisioning for the loop A/B harness.

Each ``(loop, tier, brief, repetition)`` cell runs against a directory recreated
from the brief's committed seed fixture. Recreating rather than reusing is the
fair-comparison invariant the scoreboard rests on: if one loop could inherit
another's artifacts, the acceptance grade would measure run order instead of the
loop under test.

The seed lands in a project subtree rather than at the cell root. A run is
attributed to :data:`~evals.runner.execution.EVAL_TASK_PROJECT`, and every
sandbox a cell drives (the native shell tool's, and the OpenHands container's)
picks its mount by resolving that project id under the sandbox root, so a flat
layout is one neither can bind.

Both ``brief_id`` and ``seed_dir`` arrive from authored YAML, so every path built
from them is resolved and re-checked against its root before any filesystem work
happens.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from evals.errors import (
    WorkspacePathEscapeError,
    WorkspaceSeedNotFoundError,
    WorkspaceSpecMissingError,
)
from evals.models.brief import Brief
from evals.runner.execution import EVAL_TASK_PROJECT
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_LOOP_AB_WORKSPACE_PATH_ESCAPED,
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
            EVALS_LOOP_AB_WORKSPACE_PATH_ESCAPED,
            candidate=str(candidate),
            root=str(resolved_root),
        )
        msg = f"path {str(candidate)!r} escapes the root {str(resolved_root)!r}"
        raise WorkspacePathEscapeError(msg)
    return resolved


def seed_workspace(*, brief: Brief, suite_root: Path, work_root: Path) -> CellWorkspace:
    """Recreate *brief*'s workspace from its seed fixture and return it.

    The whole cell root is removed first, not just the project subtree, so a
    repeated call yields a workspace byte-identical to the committed fixture
    regardless of what a previous run left anywhere under the mount.

    Args:
        brief: The workspace-graded executable brief to provision for.
        suite_root: Directory the brief's ``seed_dir`` is resolved against.
        work_root: Directory per-brief cell roots are created under.

    Returns:
        The provisioned :class:`CellWorkspace`.

    Raises:
        WorkspaceSpecMissingError: *brief* declares no ``workspace`` block.
        WorkspaceSeedNotFoundError: The seed fixture directory does not exist.
        WorkspacePathEscapeError: A resolved path escapes its root.
    """
    spec = brief.workspace
    if spec is None:
        msg = (
            f"brief {brief.brief_id!r} has no workspace block; it is not "
            "workspace-graded and cannot be provisioned"
        )
        raise WorkspaceSpecMissingError(msg)

    seed = _contained(Path(spec.seed_dir), suite_root)
    if not seed.is_dir():
        msg = (
            f"brief {brief.brief_id!r} seed fixture {spec.seed_dir!r} is not a "
            f"directory under {suite_root}; record it before running the A/B"
        )
        raise WorkspaceSeedNotFoundError(msg)

    work_root.mkdir(parents=True, exist_ok=True)
    root = _contained(Path(brief.brief_id), work_root)
    if root.exists():
        shutil.rmtree(root)
    workspace = CellWorkspace(root=root)
    # Re-checked after resolution even though the segments below ``root`` are
    # our own constants: ``brief_id`` reached ``root`` from authored YAML, and a
    # symlink planted in a previous run's tree could redirect the copy.
    project_dir = _contained(Path(_PROJECTS_SUBDIR) / EVAL_TASK_PROJECT, root)
    shutil.copytree(seed, project_dir)

    logger.info(
        EVALS_WORKSPACE_SEEDED,
        brief_id=brief.brief_id,
        seed_dir=spec.seed_dir,
        project=EVAL_TASK_PROJECT,
    )
    return workspace


__all__ = ["CellWorkspace", "seed_workspace"]
