# module-kind: code
"""Per-run workspace provisioning for the loop A/B harness.

Each ``(loop, tier, brief, repetition)`` cell runs against a directory recreated
from the brief's committed seed fixture. Recreating rather than reusing is the
fair-comparison invariant the scoreboard rests on: if one loop could inherit
another's artifacts, the acceptance grade would measure run order instead of the
loop under test.

Both ``brief_id`` and ``seed_dir`` arrive from authored YAML, so every path built
from them is resolved and re-checked against its root before any filesystem work
happens.
"""

import shutil
from pathlib import Path

from evals.errors import (
    WorkspacePathEscapeError,
    WorkspaceSeedNotFoundError,
    WorkspaceSpecMissingError,
)
from evals.models.brief import Brief
from synthorg.observability import get_logger
from synthorg.observability.events.evals import EVALS_WORKSPACE_SEEDED

logger = get_logger(__name__)


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
        msg = f"path {str(candidate)!r} escapes the root {str(resolved_root)!r}"
        raise WorkspacePathEscapeError(msg)
    return resolved


def seed_workspace(*, brief: Brief, suite_root: Path, work_root: Path) -> Path:
    """Recreate *brief*'s workspace from its seed fixture and return it.

    Any existing directory for the brief is removed first, so a repeated call
    yields a workspace byte-identical to the committed fixture regardless of
    what a previous run left behind.

    Args:
        brief: The workspace-graded executable brief to provision for.
        suite_root: Directory the brief's ``seed_dir`` is resolved against.
        work_root: Directory per-brief workspaces are created under.

    Returns:
        The provisioned workspace directory.

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
    work_dir = _contained(Path(brief.brief_id), work_root)
    if work_dir.exists():
        shutil.rmtree(work_dir)
    shutil.copytree(seed, work_dir)

    logger.info(
        EVALS_WORKSPACE_SEEDED,
        brief_id=brief.brief_id,
        seed_dir=spec.seed_dir,
    )
    return work_dir


__all__ = ["seed_workspace"]
