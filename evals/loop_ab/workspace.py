# module-kind: code
"""Provision a cell's workspace from the brief that declares it.

The provisioning itself is generic and lives in
:mod:`evals.harness.workspace`. What is here is the one brief-shaped fact it
does not know: a brief may declare no ``workspace`` block at all, in which case
it is not workspace-graded and there is nothing to provision, which is a
different failure from a fixture that is missing.
"""

from pathlib import Path

from evals.errors import WorkspaceSpecMissingError
from evals.harness.workspace import CellWorkspace, seed_workspace
from evals.models.brief import Brief


def seed_brief_workspace(
    *, brief: Brief, suite_root: Path, work_root: Path
) -> CellWorkspace:
    """Recreate *brief*'s workspace from its committed seed fixture.

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
    return seed_workspace(
        cell_key=brief.brief_id,
        seed_dir=spec.seed_dir,
        suite_root=suite_root,
        work_root=work_root,
    )


__all__ = ["seed_brief_workspace"]
