# module-kind: code
"""Every finished cell on disk the moment it finishes.

A sweep cell is hours of paid work, and the matrix is a sequence of them. Held
in memory until the last one returns, the whole sweep is one process crash, one
restart, one Ctrl-C away from having produced nothing: a live run spent seven
hours, measured one cell, and a second process started after it was killed with
nothing written at all.

So a cell is appended here the instant it is recorded, measured or not, and a
later sweep against the same matrix reads them back instead of paying for them
again. The file is JSON Lines because that is the shape that survives a crash:
a process killed mid-write truncates one line and leaves every earlier line
intact, where a rewritten JSON document loses the lot.

What resumes and what re-runs is not symmetric, and the asymmetry is the point.
A MEASURED cell is a result that cost hours, so it is read back. An UNAVAILABLE
one is a result that cost almost nothing (three of four cells in that live run
died in tree planning before a single leaf ran) and the operator restarting has
usually just fixed the reason it failed, so it is attempted again. Resuming an
unavailable cell would hand back the same broken report the operator restarted
to escape.
"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from io import TextIOWrapper
from pathlib import Path

from evals.errors import RecursionDepthJournalMismatchError
from evals.recursion_depth.manifest import Arm
from evals.recursion_depth.models import CellRecord, Provenance
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_RECURSION_CELL_JOURNALLED,
    EVALS_RECURSION_CELL_REPLAYED,
    EVALS_RECURSION_JOURNAL_TRUNCATED,
    EVALS_RECURSION_RESUMED,
)

logger = get_logger(__name__)

#: The file a sweep appends to, beside the report it will eventually write.
JOURNAL_NAME: str = "cells.jsonl"

#: Marks the first line, so a header can never be mistaken for a cell.
_HEADER_KIND: str = "recursion-depth-journal"


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


def _parse_cell(line: str) -> CellRecord:
    """Read one journalled cell back.

    A cell is written with its ``computed_field`` totals, so a killed sweep's
    journal reads as a report rather than as raw units. Those keys are derived,
    and ``CellRecord`` forbids extras, so they are dropped before validation
    rather than being kept out of the file: the read is where they are known to
    be redundant, and the file is where somebody looks when a sweep died.

    Derived from the model rather than listed, because a fourth total added
    later would otherwise make every journal unreadable at the moment it is
    needed most.

    Args:
        line: One journal line.

    Returns:
        The cell.
    """
    stamped = json.loads(line)
    for derived in CellRecord.model_computed_fields:
        stamped.pop(derived, None)
    return CellRecord.model_validate(stamped)


def _key_of(record: CellRecord) -> str:
    """The key *record* belongs under.

    Returns:
        The key.
    """
    return cell_key(record.depth_cap, record.arm, record.repetition)


def _comparable(provenance: Provenance) -> dict[str, object]:
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


@dataclass(frozen=True)
class ResumeState:
    """What a previous attempt at this matrix already paid for.

    Attributes:
        completed: Measured cells, keyed by :func:`cell_key`. Read back rather
            than re-run.
        sessions_spent: Agent sessions those cells consumed, re-booked against
            the sweep ceiling so a resumed sweep is bounded like one sweep
            rather than like each of its attempts.
    """

    completed: Mapping[str, CellRecord]
    sessions_spent: int

    def holds(self, key: str) -> CellRecord | None:
        """The recorded cell for *key*, when there is one.

        Returns:
            The cell, or ``None`` when this matrix position still has to run.
        """
        return self.completed.get(key)


class CellJournal:
    """Append-only record of every cell a sweep finishes."""

    __slots__ = ("_handle",)

    def __init__(self, handle: TextIOWrapper) -> None:
        self._handle = handle

    def record(self, record: CellRecord) -> None:
        """Append *record* and put it beyond this process's survival.

        Flushed and fsynced rather than left to the OS: the failure this exists
        for is the process dying, and a line sitting in a buffer at that moment
        is a line that was never written.

        Args:
            record: The finished cell, measured or unavailable.
        """
        self._handle.write(record.model_dump_json() + "\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())
        logger.info(
            EVALS_RECURSION_CELL_JOURNALLED,
            cell=_key_of(record),
            measured=record.achieved_depth is not None,
            units=len(record.units),
            tokens=record.total_tokens,
        )

    def close(self) -> None:
        """Release the file."""
        self._handle.close()


class RecordedCells:
    """The sweep's cells, remembered in memory and written down as they land.

    One owner for both halves. The driver records a cell from four different
    branches (measured, ceiling-stopped, quota-stopped, and anything else), and
    a journal call beside each of those appends is four chances to add a fifth
    branch that only remembers.
    """

    __slots__ = ("_cells", "_journal")

    def __init__(self, journal: CellJournal) -> None:
        self._journal = journal
        self._cells: list[CellRecord] = []

    def add(self, record: CellRecord) -> None:
        """Remember *record* and put it on disk.

        Args:
            record: A cell this sweep just finished.
        """
        self._cells.append(record)
        self._journal.record(record)

    def replay(self, record: CellRecord) -> None:
        """Remember *record*, which is already on disk.

        Args:
            record: A cell an earlier attempt at this matrix recorded.
        """
        self._cells.append(record)
        logger.info(
            EVALS_RECURSION_CELL_REPLAYED,
            cell=_key_of(record),
            units=len(record.units),
            tokens=record.total_tokens,
        )

    def __len__(self) -> int:
        """How many cells the sweep has recorded.

        Returns:
            The count.
        """
        return len(self._cells)

    @property
    def cells(self) -> tuple[CellRecord, ...]:
        """Every cell, in the order it entered the sweep.

        Returns:
            The cells.
        """
        return tuple(self._cells)


def _read_lines(path: Path) -> list[str]:
    """Every non-empty line of *path*.

    Returns:
        The lines, in the order they were appended.
    """
    text = path.read_text(encoding="utf-8")
    return [line for line in text.split("\n") if line.strip()]


def _refuse_foreign(path: Path, header: str, provenance: Provenance) -> None:
    """Refuse a journal that is not this sweep's.

    Args:
        path: The journal, named in the refusal.
        header: Its first line.
        provenance: What this recording is measured against.

    Raises:
        RecursionDepthJournalMismatchError: The header is unreadable, is not a
            header at all, or describes a different matrix.
    """
    try:
        stamped = json.loads(header)
    except json.JSONDecodeError as exc:
        msg = (
            f"the journal at {path} does not start with a readable header, so "
            f"the cells under it cannot be attributed to any matrix; move it "
            f"aside to record afresh"
        )
        raise RecursionDepthJournalMismatchError(msg) from exc
    if stamped.get("kind") != _HEADER_KIND:
        msg = (
            f"the file at {path} is not a recursion-depth journal; move it "
            f"aside to record afresh"
        )
        raise RecursionDepthJournalMismatchError(msg)
    wanted = _comparable(provenance)
    differing = sorted(
        field for field, value in wanted.items() if stamped.get(field) != value
    )
    if differing:
        msg = (
            f"the journal at {path} was recorded against a different sweep "
            f"({', '.join(differing)} differ), and cells from two matrices are "
            f"not one curve; move it aside to record afresh"
        )
        raise RecursionDepthJournalMismatchError(msg)


def _recorded_cells(path: Path, lines: list[str]) -> list[CellRecord]:
    """Parse the cell lines, tolerating a crash-truncated last one.

    A partial final line is the signature of the failure this journal exists
    for, so it is dropped with a warning. A partial line anywhere EARLIER is
    corruption rather than a crash, and reading past it would silently drop a
    measured cell the operator already paid for.

    Args:
        path: The journal, named in the refusal.
        lines: Every line after the header.

    Returns:
        The cells, in the order they were recorded.

    Raises:
        RecursionDepthJournalMismatchError: A line other than the last is
            unreadable.
    """
    cells: list[CellRecord] = []
    for index, line in enumerate(lines):
        try:
            cells.append(_parse_cell(line))
        except ValueError as exc:
            if index == len(lines) - 1:
                logger.warning(
                    EVALS_RECURSION_JOURNAL_TRUNCATED,
                    journal=str(path),
                    recorded_cells=len(cells),
                )
                break
            msg = (
                f"the journal at {path} is unreadable at cell {index + 1} of "
                f"{len(lines)}, which is corruption rather than an interrupted "
                f"write; move it aside to record afresh"
            )
            raise RecursionDepthJournalMismatchError(msg) from exc
    return cells


def _resume_state(cells: list[CellRecord]) -> ResumeState:
    """What *cells* let a fresh sweep skip.

    Returns:
        The resume state.
    """
    completed = {
        _key_of(cell): cell for cell in cells if cell.achieved_depth is not None
    }
    return ResumeState(
        completed=completed,
        sessions_spent=sum(cell.total_attempts for cell in completed.values()),
    )


def open_journal(
    out_dir: Path, *, provenance: Provenance, resume: bool
) -> tuple[CellJournal, ResumeState]:
    """Open the journal beside the report, reading back what it already holds.

    Args:
        out_dir: Where the report is written.
        provenance: What this recording is measured against.
        resume: Whether an existing journal is read back. ``False`` refuses to
            open onto one rather than truncating it, because the cells under it
            are hours of paid work and this is not the place to decide they are
            worthless.

    Returns:
        The open journal and what a previous attempt already paid for.

    Raises:
        RecursionDepthJournalMismatchError: A journal exists that this sweep
            must not append to.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / JOURNAL_NAME
    state = ResumeState(completed={}, sessions_spent=0)
    if not path.exists():
        return _started(path, provenance), state
    if not resume:
        msg = (
            f"a journal already exists at {path}; pass --resume to continue "
            f"that sweep, or move it aside to record afresh"
        )
        raise RecursionDepthJournalMismatchError(msg)
    lines = _read_lines(path)
    if not lines:
        # An empty file is a journal whose header never reached the disk, so it
        # attributes nothing. Appending cells under no header would make every
        # one of them unreadable at the next resume, which is the failure this
        # whole file exists to prevent, arrived at from the other side.
        return _started(path, provenance), state
    _refuse_foreign(path, lines[0], provenance)
    state = _resume_state(_recorded_cells(path, lines[1:]))
    logger.info(
        EVALS_RECURSION_RESUMED,
        journal=str(path),
        completed_cells=len(state.completed),
        sessions_spent=state.sessions_spent,
    )
    return CellJournal(path.open("a", encoding="utf-8", newline="")), state


def _started(path: Path, provenance: Provenance) -> CellJournal:
    """Open *path* fresh and put its header beyond this process's survival.

    Args:
        path: The journal file.
        provenance: What this recording is measured against.

    Returns:
        The open journal.
    """
    handle = path.open("w", encoding="utf-8", newline="")
    handle.write(json.dumps({"kind": _HEADER_KIND} | _comparable(provenance)) + "\n")
    handle.flush()
    os.fsync(handle.fileno())
    return CellJournal(handle)


__all__ = [
    "JOURNAL_NAME",
    "CellJournal",
    "RecordedCells",
    "ResumeState",
    "cell_key",
    "open_journal",
]
