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

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from evals.errors import WorkspacePathEscapeError, WorkspaceSeedNotFoundError
from evals.runner.execution import EVAL_TASK_PROJECT
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_HARNESS_WORKSPACE_LINK_DROPPED,
    EVALS_HARNESS_WORKSPACE_LINK_REBASED,
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


def reseed_workspace(
    *, cell_key: str, source: CellWorkspace, work_root: Path
) -> CellWorkspace:
    """Recreate one unit's workspace from a tree an earlier stage BUILT.

    The counterpart to :func:`seed_workspace` for the contract stage, whose
    output is what every unit of its cell is then recreated from. Separate
    rather than a parameter on the seeder because the two copy sources differ
    in the one way that matters: a committed fixture is ours and a contract
    tree was written by an agent, so its symlinks are swept the way every other
    agent-authored tree is before being copied somewhere it will be read.

    Takes the WORKSPACE rather than its project path, because the path alone
    cannot be checked. ``CellWorkspace.project_dir`` is derived by joining, so
    it names wherever that join now leads: the contract session can write in
    this tree, and replacing the project subtree (or the ``projects`` directory
    above it) with a link makes ``is_dir()`` follow it and
    ``copytree(symlinks=True)`` copy what it reaches. The sweep below would not
    catch that, because by then the host's files are real files in the copy
    rather than links to them. Resolving against the source's own root
    forecloses both shapes, and it is the check
    :func:`existing_workspace` already applies for the same reason.

    Args:
        cell_key: Names the unit's tree under *work_root*. Reaches this from
            authored YAML or from a plan an agent wrote, so it is resolved and
            re-checked against its root like any other untrusted segment.
        source: The workspace whose project tree is copied, which an agent
            wrote into.
        work_root: Directory per-unit roots are created under.

    Returns:
        The provisioned :class:`CellWorkspace`.

    Raises:
        WorkspaceSeedNotFoundError: The source project tree is not a directory.
        WorkspacePathEscapeError: A resolved path escapes its root.
    """
    source_project = _contained(Path(_PROJECTS_SUBDIR) / EVAL_TASK_PROJECT, source.root)
    if not source_project.is_dir():
        msg = (
            f"unit {cell_key!r} cannot be seeded from {source_project}, which is "
            f"not a directory; the stage that was to build it produced no tree"
        )
        raise WorkspaceSeedNotFoundError(msg)

    work_root.mkdir(parents=True, exist_ok=True)
    root = _contained(Path(cell_key), work_root)
    if root.exists():
        shutil.rmtree(root)
    project_dir = _contained(Path(_PROJECTS_SUBDIR) / EVAL_TASK_PROJECT, root)
    shutil.copytree(
        source_project, project_dir, symlinks=True, ignore_dangling_symlinks=True
    )
    drop_escaping_links(project_dir, anchor=source_project)

    logger.info(
        EVALS_WORKSPACE_SEEDED,
        cell_key=cell_key,
        seed_dir=str(source_project),
        project=EVAL_TASK_PROJECT,
    )
    return CellWorkspace(root=root)


def existing_workspace(*, cell_key: str, work_root: Path) -> CellWorkspace | None:
    """The tree a previous run left at *cell_key*, if it is still there.

    The counterpart to :func:`seed_workspace` and deliberately not a variant of
    it: recreating from the seed is what makes each unit's grade its own, and a
    resume is the one caller that must NOT recreate, because the tree on disk
    is the delivery it is trying not to pay for twice.

    Answers ``None`` rather than raising when the tree is gone, because that is
    an ordinary state (an operator cleared the work root between attempts) and
    the caller's answer to it is to run the unit again.

    Args:
        cell_key: Names the tree under *work_root*. Reaches this from a plan an
            agent wrote, so it is resolved and re-checked against its root like
            any other untrusted segment.
        work_root: Directory per-cell roots live under.

    Returns:
        The workspace, or ``None`` when nothing was built there.

    Raises:
        WorkspacePathEscapeError: A resolved path escapes its root.
    """
    root = _contained(Path(cell_key), work_root)
    # The project subtree is re-checked too, for the reason ``seed_workspace``
    # re-checks it: the tree being read back is one an AGENT could write into,
    # so it could have replaced the subtree with a link to somewhere else. Here
    # the stakes are the higher half of that pair, because a resume MOUNTS what
    # it finds as a merge's child rather than copying a fixture over it.
    project_dir = _contained(Path(_PROJECTS_SUBDIR) / EVAL_TASK_PROJECT, root)
    if not project_dir.is_dir():
        return None
    return CellWorkspace(root=root)


def drop_escaping_links(mounted: Path, *, anchor: Path) -> None:
    """Settle every symlink under *mounted* against the copy it now sits in.

    Judged against the tree's ORIGINAL location rather than the copy, because a
    relative link was authored against that tree and is what the agent meant it
    to reach. A link that stays inside its own delivery is legitimate and kept;
    anything else named a place the copy has no business reading and is
    removed.

    Kept is not the same as left alone, though, and the difference is only
    visible once the tree has moved. A RELATIVE internal link keeps meaning
    what it meant, because the thing it points at was copied alongside it. An
    ABSOLUTE one still names the original tree, which nothing mounts beside
    the copy: it is a dangling link in every reader, and in a container it
    also carries a host path in. So an absolute link resolving inside the
    anchor is rebased onto the copy, expressed relatively so it survives the
    next copy too, and one that cannot be rebased is dropped like any other
    escape.

    Shared by every place an agent-authored tree is copied somewhere it will be
    read: a merge's mounted children, an oracle's staging directory, and the
    detached copy a reviewer works in. Two copies of this would be one copy
    away from disagreeing, and one of them was written without it.

    Args:
        mounted: The copied tree to sweep.
        anchor: The tree's own original location, the only region a link may
            resolve into.
    """
    resolved_anchor = anchor.resolve()
    resolved_mount = mounted.resolve()
    for path in mounted.rglob("*"):
        if not path.is_symlink():
            continue
        # Resolved from where the link SAT IN THE ORIGINAL TREE, not from the
        # copy. A relative link was authored against the original, so resolving
        # it inside `mounted` lands under `mounted`, which is never inside
        # `anchor`: every legitimate internal link would then be judged an
        # escape and deleted. An absolute link ignores the base either way,
        # which is the case this is actually looking for.
        relative_to_tree = path.relative_to(mounted)
        origin = (resolved_anchor / relative_to_tree).parent
        target = (origin / path.readlink()).resolve()
        if target != resolved_anchor and resolved_anchor not in target.parents:
            logger.warning(
                EVALS_HARNESS_WORKSPACE_LINK_DROPPED,
                link=str(relative_to_tree),
                mounted_as=mounted.name,
            )
            path.unlink()
        elif path.readlink().is_absolute():
            _rebase_link(
                path,
                at=relative_to_tree,
                target=target,
                anchor=resolved_anchor,
                copy=resolved_mount,
            )


def _rebase_link(
    path: Path, *, at: Path, target: Path, anchor: Path, copy: Path
) -> None:
    """Repoint one absolute internal link at the copied tree.

    The new target is worked out entirely in RESOLVED space and written
    relatively. Mixing the two spaces is wrong wherever the tree root is
    itself reached through a link (a macOS ``/tmp`` is), and a relative link
    is read against its own directory whichever alias got there, so the
    result is right under either.

    Args:
        path: The link itself, to rewrite in place.
        at: Where it sits, relative to the tree root.
        target: What it resolves to inside *anchor*.
        anchor: The original tree's resolved location.
        copy: The copied tree's resolved location.
    """
    inside = copy if target == anchor else copy / target.relative_to(anchor)
    rebased = os.path.relpath(inside, start=(copy / at).parent)
    logger.info(
        EVALS_HARNESS_WORKSPACE_LINK_REBASED,
        link=str(at),
        mounted_as=copy.name,
    )
    path.unlink()
    path.symlink_to(rebased)


def detach_workspace(source: CellWorkspace, root: Path) -> CellWorkspace:
    """Copy *source*'s project tree under *root* and answer a workspace on it.

    For a session that must READ and RUN a tree without being able to change
    the one that gets graded. The copy is the whole isolation: a read-only tool
    set is not, because a session that can run shell commands in a tree can
    write to it whatever its file tools allow, and the completion-oracle
    reviewer is required to run disconfirming commands.

    Symlinks are copied as links rather than followed, then any that resolve
    outside the source tree are dropped, exactly as the merge mount and the
    oracle staging do: a link in an agent-authored tree names a host path the
    agent chose, and following it here would pull that path into a directory
    about to be mounted into a container.

    Args:
        source: The workspace to copy from.
        root: An empty directory to build the detached copy under.

    Returns:
        A :class:`CellWorkspace` on the copy.

    Raises:
        WorkspacePathEscapeError: A resolved path escapes its root.
    """
    project_dir = _contained(Path(_PROJECTS_SUBDIR) / EVAL_TASK_PROJECT, root)
    shutil.copytree(
        source.project_dir,
        project_dir,
        symlinks=True,
        ignore_dangling_symlinks=True,
    )
    drop_escaping_links(project_dir, anchor=source.project_dir)
    return CellWorkspace(root=root)


__all__ = [
    "CellWorkspace",
    "detach_workspace",
    "reseed_workspace",
    "seed_workspace",
]
