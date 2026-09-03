# module-kind: code
"""What a unit of the tree IS, and what it produced.

A unit is one leaf or one assembly: it has a key naming its tree under the
cell, a workspace recreated from the specification's seed, and a verdict on
what it built. All four of those are asked by the leaf loop, the merge loop
and the resume path alike, and none of them needs anything about running a
session, which is why they live apart from it.

The one rule the whole module exists to state: delivery is read off the TREE.
Neither the planner's declaration nor the unit's own paperwork decides it,
because a declaration is a guess made before the tree existed and paperwork is
what a unit writes about itself.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from evals.harness.workspace import (
    CellWorkspace,
    existing_workspace,
    reseed_workspace,
    seed_workspace,
)
from synthorg.core.task import Task
from synthorg.engine.artifacts.expected_artifact_check import (
    ArtifactPresence,
    missing_expected_artifacts,
)
from synthorg.engine.artifacts.workspace_fingerprint import (
    WorkspaceFingerprint,
    fingerprint_tree,
)

#: Every file a unit produced, as ``(posix relative path, content key)``. The
#: product's own fingerprint under the harness's name, because "what does this
#: tree hold" has one answer and a second implementation of it is a second
#: place for a link to be followed or a rewrite to be missed.
type UnitFingerprint = WorkspaceFingerprint

#: What sits in a unit's tree without being anything that unit produced: the
#: pieces a merge was handed, the unit's own paperwork, and the brief it
#: started from. Everything else under the project directory IS the work.
_NOT_PRODUCED: Final[frozenset[str]] = frozenset(
    {".children", ".synthorg", "README.md"}
)


def leaf_unit_key(task_id: str) -> str:
    """What a leaf's tree is called under its cell.

    The single owner of the format, because a resume reaches for a tree a
    previous attempt built by rebuilding this string: a second spelling would
    look in an empty directory and re-run work already paid for.

    Returns:
        The key.
    """
    return f"leaf-{task_id}"


def merge_unit_key(task_id: str) -> str:
    """What an assembly's tree is called under its cell.

    Returns:
        The key.
    """
    return f"merge-{task_id}"


def unit_workspace(
    *,
    cell_key: str,
    unit_key: str,
    spec_dir: Path,
    work_root: Path,
    contract: CellWorkspace | None = None,
) -> CellWorkspace:
    """Recreate one unit's workspace from whatever this cell builds against.

    The contract's tree when the cell ran one, and the specification's committed
    seed otherwise. Recreating from the contract is the whole mechanism by which
    a unit finds the shared names already in its checkout: nothing is handed to
    it, nothing is mounted, and no sibling's workspace is read.

    Args:
        cell_key: Names the run this unit belongs to.
        unit_key: Names the unit within that run.
        spec_dir: The specification directory, which holds the seed.
        work_root: Directory per-unit trees are created under.
        contract: The tree the cell's contract stage left, or ``None`` for an
            arm that runs without one.

    Returns:
        The provisioned workspace.
    """
    if contract is not None:
        return reseed_workspace(
            cell_key=f"{cell_key}/{unit_key}",
            source=contract,
            work_root=work_root,
        )
    return seed_workspace(
        cell_key=f"{cell_key}/{unit_key}",
        seed_dir="seed",
        suite_root=spec_dir,
        work_root=work_root,
    )


def built_unit_workspace(
    *, cell_key: str, unit_key: str, work_root: Path
) -> CellWorkspace | None:
    """The tree a previous attempt left for one unit, if it is still there.

    Args:
        cell_key: Names the run this unit belongs to.
        unit_key: Names the unit within that run.
        work_root: Directory per-unit trees live under.

    Returns:
        The workspace, or ``None`` when nothing was built there.
    """
    return existing_workspace(cell_key=f"{cell_key}/{unit_key}", work_root=work_root)


def probe_artifacts(task: Task, workspace: CellWorkspace) -> ArtifactPresence:
    """Ask *workspace* what it holds against *task*'s declared paths.

    Read off disk rather than from the session's account of itself: a run
    reports the tools it called, and whether those calls left the declared file
    behind is a different question that only the tree answers.

    Taken once BEFORE a session and once after, because the question delivery
    turns on is what this run produced rather than what the workspace happens
    to contain: the seed is recreated per unit and a declaration satisfied by
    the seed is not work.

    Args:
        task: The unit's task, carrying its declared artifacts.
        workspace: The tree it ran against.

    Returns:
        What each probeable declaration says right now.
    """
    return missing_expected_artifacts(
        task.artifacts_expected, workspace=workspace.project_dir
    )


def files_changed(before: UnitFingerprint, after: UnitFingerprint) -> int:
    """How many produced files a unit's session touched.

    Counted by PATH, not by fingerprint entry: a rewrite drops one
    ``(path, content_key)`` pair and adds another, and the symmetric
    difference of the raw fingerprints would count that one touched file
    twice. Distinct from ``produced_tree(...) == baseline``, which only asks
    whether anything changed at all.

    Args:
        before: The unit's tree before the session ran.
        after: The unit's tree after.

    Returns:
        The count.
    """
    before_by_path = dict(before)
    after_by_path = dict(after)
    return sum(
        before_by_path.get(path) != after_by_path.get(path)
        for path in before_by_path.keys() | after_by_path.keys()
    )


@dataclass(frozen=True, slots=True)
class UnitDelivery:
    """What a unit produced, kept apart from whether what it produced stands up.

    Two questions with one answer between them is what this separates. A merge
    that assembles the whole package and does not copy its children's tests up
    to the workspace root collects no tests, because the grader runs pytest at
    that root and ``.children/`` is dot-prefixed, so pytest's own
    ``norecursedirs`` never descends into it. The unit had therefore built
    something and failed a check, and both facts arrived at its parent as the
    single word ``[DID NOT DELIVER]``.

    Measured on a live cap-2 cell, that is not a labelling nicety. Four of the
    root's seven pieces were marked that way while holding 46, 46, 41 and 36
    Python modules between them; the root was told most of its inputs had
    failed, and across six attempts and 119 turns it wrote nothing at all. The
    cell scored 0 of 42. Correlation between the mark and whether the merge
    happened to copy a test file up a directory was exact, six of six.

    So the parent is told what it can act on. ``produced`` decides whether
    there is anything there to assemble; ``reason`` says what is wrong with it,
    which is a different sentence and reads as one.

    Attributes:
        produced: Whether the unit's own tree changed. The only fact that
            answers "is there something here".
        reason: Why this is not a clean delivery, empty when it is. Set with
            ``produced`` false when nothing was built, and with ``produced``
            true when something was built that does not stand up.
        note: What the parent should know that is NOT a reason to doubt the
            delivery, empty when there is nothing to say. Held apart from
            ``reason`` because ``delivered`` reads any reason as a failure,
            so a branch explaining that a check decided NOTHING was marking
            the leaf undelivered on the strength of its own explanation. Both
            such branches say the tree is the evidence, and the tree changed.
        workspace_files_changed: How many files differ between the tree
            before and after, so "turns were spent and nothing changed" is
            readable from the record without opening a transcript. ``None``
            only when reconstructed from a recording made before this field
            existed; every delivery this module computes fresh carries a
            real count, including zero.
    """

    produced: bool
    reason: str
    workspace_files_changed: int | None = None
    note: str = ""

    def __post_init__(self) -> None:
        """Refuse a delivery that both doubts itself and says not to.

        Raises:
            ValueError: If ``reason`` and ``note`` are both set.
        """
        if self.reason and self.note:
            msg = "a delivery cannot carry both a reason and a note"
            raise ValueError(msg)

    @property
    def delivered(self) -> bool:
        """Did this unit both produce something and pass its own checks?

        Returns:
            True only when both hold. This is the scoring flag; it is
            deliberately NOT what the parent's brief renders, because a
            subtree that built a package and failed a check is not the same
            input as one that built nothing.
        """
        return self.produced and not self.reason


def delivery_of(
    *, produced: bool, detail: str, note: str, files_changed: int | None
) -> UnitDelivery:
    """Rebuild a delivery from the two texts a record keeps apart.

    One owner because three call sites rebuild these, and a rule written
    three times is the shape the note exists to stop: a note that lands in
    ``reason`` recomputes ``delivered`` as false and quietly disagrees with
    the run being replayed.

    Args:
        produced: Whether the recorded unit's tree changed.
        detail: Why the record says the unit did not deliver, empty when it
            did.
        note: What the record says a reader should know that is not a reason.
        files_changed: The recorded file count, ``None`` before it existed.

    Returns:
        A delivery whose ``delivered`` matches the record it came from.
    """
    return UnitDelivery(
        produced=produced,
        reason=detail,
        note=note,
        workspace_files_changed=files_changed,
    )


def produced_tree(workspace: CellWorkspace) -> UnitFingerprint:
    """Fingerprint what a unit has produced, ignoring what it was handed.

    The one question every delivery verdict in this harness asks, so there is
    one answer to it. The two available proxies are both wrong in the same
    direction, and both were measured wrong on live cells:

    A PLANNER-DECLARED path is a guess made before the tree existed, so a leaf
    writing four modules under names the planner did not predict reads as
    having left every declared path as it found it. Measured on a live cap-1
    cell: two leaves, one of 4 files and one of 10, both booked as producing
    nothing. That verdict feeds the survival denominator, so it does not merely
    mislabel the leaf, it removes it from the metric.

    A merge's own report is the same mistake one level up, and additionally
    briefs the parent ``[DID NOT DELIVER]``; see
    ``results/merge-delivery-false-negative/``.

    Asking the tree needs no guess about which paths matter and no knowledge of
    which tools mutate. What a unit DECLARED is still recorded, because a
    planner over-declaring is worth seeing. It just does not decide.

    Args:
        workspace: The unit's tree.

    Returns:
        Each produced file as ``(relative path, content key)``. What the unit
        was HANDED is excluded by name among the root's own children, which is
        the one thing this knows and the product's fingerprint cannot: the
        harness mounted those itself.
    """
    return fingerprint_tree(workspace.project_dir, exclude=_NOT_PRODUCED)


__all__ = [
    "UnitDelivery",
    "UnitFingerprint",
    "built_unit_workspace",
    "delivery_of",
    "files_changed",
    "leaf_unit_key",
    "merge_unit_key",
    "probe_artifacts",
    "produced_tree",
    "unit_workspace",
]
