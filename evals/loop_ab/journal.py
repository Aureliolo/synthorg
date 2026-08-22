# module-kind: code
"""What the A/B's rows are called on disk, and which ones a resume buys back.

The durability, the header check and the crash tolerance live once, in
:mod:`evals.harness.journal`. This module supplies the three things that are
this matrix's own: what a row is filed under, what identifies the matrix it
belongs to, and which rows are worth reading back rather than paying for again.

A MEASURED row is a result that cost real provider spend across every
repetition of its cell, so it is read back. An UNAVAILABLE one recorded a
failure rather than a measurement, and the operator restarting has usually just
fixed the reason it failed, so it is attempted again.
"""

from collections.abc import Mapping
from pathlib import Path

from evals.harness.journal import JournalSpec, RecordedCells, ResumeState, open_journal
from evals.loop_ab.models import LoopBriefRow, Provenance

#: The file a recording appends to, beside the scoreboard it will write.
JOURNAL_NAME: str = "rows.jsonl"

#: Names this journal in its own header, so a recursion-depth journal opened
#: here is refused rather than parsed into confusion.
JOURNAL_KIND: str = "loop-ab"


def row_key(loop_type: str, capability: str, brief_id: str) -> str:
    """The name one row is journalled under.

    The single owner of the format. A resume matches recorded rows to planned
    ones by this string alone, so two spellings of it would silently re-run
    every cell the matrix had already paid for.

    Args:
        loop_type: Which loop ran.
        capability: The capability rung it ran at.
        brief_id: The brief it ran.

    Returns:
        The key.
    """
    return f"{loop_type}/{capability}/{brief_id}"


def _key_of(record: LoopBriefRow) -> str:
    """The key *record* belongs under.

    Returns:
        The key.
    """
    return row_key(str(record.loop_type), str(record.capability), str(record.brief_id))


def _measured(record: LoopBriefRow) -> bool:
    """Whether *record* is worth reading back rather than re-running.

    Returns:
        True for a row carrying a measurement.
    """
    return record.measurement is not None


SPEC: JournalSpec[LoopBriefRow] = JournalSpec(
    kind=JOURNAL_KIND,
    filename=JOURNAL_NAME,
    record_type=LoopBriefRow,
    key_of=_key_of,
    resumable=_measured,
)


def matrix_identity(provenance: Provenance) -> Mapping[str, object]:
    """What a resume must agree on for its rows to belong to one recording.

    Everything except ``generated_at``, which is the one field that MUST differ
    between the run that wrote a row and the run reading it back.

    Args:
        provenance: What this recording is measured against.

    Returns:
        The comparable fields.
    """
    stamped = provenance.model_dump(mode="json")
    del stamped["generated_at"]
    return stamped


def open_row_journal(
    out_dir: Path, *, provenance: Provenance, resume: bool
) -> tuple[RecordedCells[LoopBriefRow], ResumeState[LoopBriefRow]]:
    """Open the recording's journal beside its scoreboard.

    Args:
        out_dir: Where the scoreboard is written.
        provenance: What this recording is measured against.
        resume: Whether an existing journal for this matrix is continued.

    Returns:
        The sink rows are recorded to, and what a previous attempt paid for.

    Raises:
        HarnessJournalMismatchError: A journal exists that this recording must
            not append to.
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
    "matrix_identity",
    "open_row_journal",
    "row_key",
]
