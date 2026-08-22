# module-kind: code
"""What the sweep writes down as it goes, and what a resume buys back with it.

The durability, the header check and the crash tolerance live once, in
:mod:`evals.harness.journal`. This module supplies what is this sweep's own:
what a row is filed under, what identifies the matrix it belongs to, and which
rows are worth reading back rather than paying for again.

TWO journals, because a cell and a session are different units of loss. A cell
is one ``(depth cap, arm, repetition)`` run: it plans a tree, builds every leaf
in its own container, assembles every node from the bottom up and hands the
root to the held-out oracle, which is tens of agent sessions and hours of real
spend. Writing only the finished cell meant a cell killed at hour six left
nothing at all: not the units it had built, not what they cost, not the tree it
was building against. So every SESSION is journalled the moment it returns
(``progress.jsonl``) and the finished cell still is too (``cells.jsonl``): the
first is what a killed run leaves behind and what a resume continues from, the
second is the result.

Which rows a resume buys back is not symmetric, and the asymmetry is the point.
A MEASURED cell is a result that cost hours, so it is read back. An UNAVAILABLE
one cost almost nothing (three of four cells in a live run died in tree planning
before a single leaf ran) and the operator restarting has usually just fixed the
reason it failed, so it is attempted again. Resuming an unavailable cell would
hand back the same broken report the operator restarted to escape.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from evals.harness.journal import JournalSpec, RecordedCells, ResumeState, open_journal
from evals.recursion_depth.manifest import Arm
from evals.recursion_depth.models import (
    CellProgressRecord,
    CellRecord,
    PlannedTreeRecord,
    Provenance,
    UnitRecord,
)

#: The file a sweep appends finished cells to, beside the report it will
#: eventually write.
JOURNAL_NAME: str = "cells.jsonl"

#: The file a sweep appends every finished SESSION to, so a cell killed
#: part-way leaves what it built rather than nothing.
PROGRESS_NAME: str = "progress.jsonl"

#: Names this journal in its own header, so a file from another harness is
#: refused rather than parsed into confusion.
JOURNAL_KIND: str = "recursion-depth"

#: The same, for the finer journal. Distinct from :data:`JOURNAL_KIND` so the
#: two files cannot be read as each other: they sit in one directory and hold
#: different row models.
PROGRESS_KIND: str = "recursion-depth-progress"


def cell_key(depth_cap: int, arm: Arm, repetition: int) -> str:
    """The name one cell is journalled under.

    The single owner of the format. A resume matches recorded cells to planned
    ones by this string alone, so two spellings of it would silently re-run
    every cell the sweep had already paid for.

    Args:
        depth_cap: The ``max_depth`` the run was allowed.
        arm: Gated or ungated.
        repetition: Zero-based index within the cell.

    Returns:
        The key.
    """
    return f"d{depth_cap}-{arm.value}-r{repetition}"


def _key_of(record: CellRecord) -> str:
    """The key *record* belongs under.

    Returns:
        The key.
    """
    return cell_key(record.depth_cap, record.arm, record.repetition)


def _measured(record: CellRecord) -> bool:
    """Whether *record* is worth reading back rather than re-running.

    Returns:
        True for a measured cell.
    """
    return record.achieved_depth is not None


SPEC: JournalSpec[CellRecord] = JournalSpec(
    kind=JOURNAL_KIND,
    filename=JOURNAL_NAME,
    record_type=CellRecord,
    key_of=_key_of,
    resumable=_measured,
)


def progress_key(depth_cap: int, arm: Arm, repetition: int, unit_id: str) -> str:
    """The name one session is journalled under.

    Keyed per SESSION rather than per cell, because a cell has many of these
    and a per-cell key would file them all under one name.

    Args:
        depth_cap: The ``max_depth`` the run was allowed.
        arm: Gated or ungated.
        repetition: Zero-based index within the cell.
        unit_id: What the session produced.

    Returns:
        The key.
    """
    return f"{cell_key(depth_cap, arm, repetition)}/{unit_id}"


def _progress_key_of(record: CellProgressRecord) -> str:
    """The key *record* belongs under.

    Returns:
        The key.
    """
    return progress_key(
        record.depth_cap, record.arm, record.repetition, str(record.unit.unit_id)
    )


def _always(_record: CellProgressRecord) -> bool:
    """Whether *record* is worth reading back rather than re-running.

    Always, and unlike a cell there is no judgement in it: this row exists
    because a session RAN, and re-running one buys the same work a second time
    whatever it produced. Whether the cell it belongs to can be continued at
    all is a separate question, decided against the trees still on disk.

    Returns:
        True.
    """
    return True


PROGRESS_SPEC: JournalSpec[CellProgressRecord] = JournalSpec(
    kind=PROGRESS_KIND,
    filename=PROGRESS_NAME,
    record_type=CellProgressRecord,
    key_of=_progress_key_of,
    resumable=_always,
)


@dataclass(frozen=True)
class CellProgress:
    """What an earlier attempt at one cell got through.

    Attributes:
        plan: The tree it was building against, absent when it died before
            planning finished.
        units: Every session it completed, in the order they ran.
    """

    plan: PlannedTreeRecord | None = None
    units: tuple[UnitRecord, ...] = ()


@dataclass(frozen=True)
class ResumedProgress:
    """What previous attempts at this matrix left behind.

    Two figures rather than one, because they answer different questions and
    the second is not derivable from the first. ``cells`` is what this sweep
    continues from, which holds ONE row per unit id: a cell re-run after an
    unusable resume supersedes its own earlier rows. ``sessions_spent`` counts
    every row ever written, superseded ones included, because that money left
    the account whichever attempt spent it.

    Attributes:
        cells: Each cell key mapped to what it got through.
        sessions_spent: Agent sessions every recorded row accounts for.
    """

    cells: Mapping[str, CellProgress]
    sessions_spent: int

    def holds(self, key: str) -> CellProgress:
        """What cell *key* got through, empty when it has not started.

        Returns:
            The progress.
        """
        return self.cells.get(key, CellProgress())


def progress_by_cell(
    state: ResumeState[CellProgressRecord],
) -> Mapping[str, CellProgress]:
    """Group every journalled session under the cell that ran it.

    A cell re-run after an unusable resume appends a SECOND set of rows for the
    same unit ids. The later ones are what the trees on disk hold, so the plan
    is taken from the last planning row and the units are read forward, which
    leaves the newest row per unit id in place.

    Args:
        state: What previous attempts recorded.

    Returns:
        Each cell key mapped to what it got through.
    """
    plans: dict[str, PlannedTreeRecord] = {}
    units: dict[str, dict[str, UnitRecord]] = {}
    for record in state.recorded:
        key = cell_key(record.depth_cap, record.arm, record.repetition)
        if record.plan is not None:
            # A fresh plan starts a fresh attempt: the units recorded under the
            # previous one belong to a tree this cell is no longer building.
            plans[key] = record.plan
            units[key] = {}
        units.setdefault(key, {})[str(record.unit.unit_id)] = record.unit
    return {
        key: CellProgress(plan=plans.get(key), units=tuple(recorded.values()))
        for key, recorded in units.items()
    }


class CellUnits:
    """One cell's sessions, remembered in memory and written down as they land.

    One owner for both halves, for the reason
    :class:`~evals.harness.journal.RecordedCells` has one: a cell records a
    session from several branches, and a journal call beside each of those is
    one chance per branch to add another that only remembers.
    """

    __slots__ = ("_arm", "_depth_cap", "_records", "_repetition", "_units")

    def __init__(
        self,
        records: RecordedCells[CellProgressRecord],
        *,
        depth_cap: int,
        arm: Arm,
        repetition: int,
    ) -> None:
        self._records = records
        self._depth_cap = depth_cap
        self._arm = arm
        self._repetition = repetition
        self._units: list[UnitRecord] = []

    def append(
        self, unit: UnitRecord, *, plan: PlannedTreeRecord | None = None
    ) -> None:
        """Put *unit* on disk, then remember it.

        Written FIRST, so a session this run claims is never one the journal
        cannot show.

        Args:
            unit: What the session just produced.
            plan: The tree, on the planning session alone.
        """
        self._records.add(
            CellProgressRecord(
                depth_cap=self._depth_cap,
                arm=self._arm,
                repetition=self._repetition,
                unit=unit,
                plan=plan,
            )
        )
        self._units.append(unit)

    def replay(self, unit: UnitRecord) -> None:
        """Remember *unit*, which an earlier attempt already recorded.

        Args:
            unit: A session this cell does not have to run again.
        """
        self._units.append(unit)

    @property
    def records(self) -> tuple[UnitRecord, ...]:
        """Every session of this cell, in the order it ran.

        Returns:
            The unit records.
        """
        return tuple(self._units)

    def __len__(self) -> int:
        """How many sessions this cell has run or replayed.

        Returns:
            The count.
        """
        return len(self._units)


def matrix_identity(provenance: Provenance) -> Mapping[str, object]:
    """What a resume must agree on for its cells to belong to one sweep.

    Everything except ``generated_at``, which is the one field that MUST differ
    between the run that wrote a cell and the run reading it back.

    Args:
        provenance: What this recording is measured against.

    Returns:
        The comparable fields.
    """
    stamped = provenance.model_dump(mode="json")
    del stamped["generated_at"]
    return stamped


def sessions_spent(state: ResumeState[CellProgressRecord]) -> int:
    """Agent sessions previous attempts at this matrix already consumed.

    Re-booked against the sweep ceiling so a sweep resumed four times is
    bounded like one sweep rather than like each of its attempts.

    Read off the SESSION rows, which is the one place every session appears
    exactly once: the sweep books its ceiling at the same three points it
    records one of these, and a cell row is those same sessions added up again.
    Reading both would double-count; reading the cell rows alone would miss
    every session of a cell that died before finishing, and that money is gone
    from the account whether or not the cell is attempted again.

    Args:
        state: What previous attempts paid for.

    Returns:
        The session count.
    """
    return sum(record.unit.attempts for record in state.recorded)


def open_cell_journal(
    out_dir: Path, *, provenance: Provenance, resume: bool
) -> tuple[RecordedCells[CellRecord], ResumeState[CellRecord]]:
    """Open the sweep's journal beside its report.

    Args:
        out_dir: Where the report is written.
        provenance: What this recording is measured against.
        resume: Whether an existing journal for this matrix is continued.

    Returns:
        The sink cells are recorded to, and what a previous attempt paid for.

    Raises:
        HarnessJournalMismatchError: A journal exists that this sweep must not
            append to.
    """
    journal, state = open_journal(
        out_dir,
        SPEC,
        identity=matrix_identity(provenance),
        resume=resume,
    )
    return RecordedCells(journal, SPEC), state


def open_progress_journal(
    out_dir: Path, *, provenance: Provenance, resume: bool
) -> tuple[RecordedCells[CellProgressRecord], ResumedProgress]:
    """Open the sweep's session journal beside its report.

    Args:
        out_dir: Where the report is written.
        provenance: What this recording is measured against.
        resume: Whether an existing journal for this matrix is continued.

    Returns:
        The sink sessions are recorded to, and what previous attempts left.

    Raises:
        HarnessJournalMismatchError: A journal exists that this sweep must not
            append to.
    """
    journal, state = open_journal(
        out_dir,
        PROGRESS_SPEC,
        identity=matrix_identity(provenance),
        resume=resume,
    )
    return RecordedCells(journal, PROGRESS_SPEC), ResumedProgress(
        cells=progress_by_cell(state),
        sessions_spent=sessions_spent(state),
    )


__all__ = [
    "JOURNAL_KIND",
    "JOURNAL_NAME",
    "PROGRESS_KIND",
    "PROGRESS_NAME",
    "PROGRESS_SPEC",
    "SPEC",
    "CellProgress",
    "CellUnits",
    "ResumedProgress",
    "cell_key",
    "matrix_identity",
    "open_cell_journal",
    "open_progress_journal",
    "progress_by_cell",
    "progress_key",
    "sessions_spent",
]
