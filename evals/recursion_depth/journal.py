# module-kind: code
"""What the sweep's cells are called on disk, and which ones a resume buys back.

The durability, the header check and the crash tolerance live once, in
:mod:`evals.harness.journal`. This module supplies the three things that are
this sweep's own: what a cell is filed under, what identifies the matrix it
belongs to, and which cells are worth reading back rather than paying for
again.

That last one is not symmetric, and the asymmetry is the point. A MEASURED cell
is a result that cost hours, so it is read back. An UNAVAILABLE one cost almost
nothing (three of four cells in a live run died in tree planning before a single
leaf ran) and the operator restarting has usually just fixed the reason it
failed, so it is attempted again. Resuming an unavailable cell would hand back
the same broken report the operator restarted to escape.
"""

from collections.abc import Mapping
from pathlib import Path

from evals.harness.journal import JournalSpec, RecordedCells, ResumeState, open_journal
from evals.recursion_depth.manifest import Arm
from evals.recursion_depth.models import CellRecord, Provenance

#: The file a sweep appends to, beside the report it will eventually write.
JOURNAL_NAME: str = "cells.jsonl"

#: Names this journal in its own header, so a file from another harness is
#: refused rather than parsed into confusion.
JOURNAL_KIND: str = "recursion-depth"


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


def sessions_spent(state: ResumeState[CellRecord]) -> int:
    """Agent sessions the resumed cells already consumed.

    Re-booked against the sweep ceiling so a sweep resumed four times is
    bounded like one sweep rather than like each of its attempts.

    Args:
        state: What a previous attempt paid for.

    Returns:
        The session count.
    """
    return sum(cell.total_attempts for cell in state.completed.values())


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


__all__ = [
    "JOURNAL_KIND",
    "JOURNAL_NAME",
    "SPEC",
    "cell_key",
    "matrix_identity",
    "open_cell_journal",
    "sessions_spent",
]
