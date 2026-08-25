# module-kind: code
"""Every finished unit of paid work on disk the moment it finishes.

A recording matrix is a sequence of cells, each of which is minutes to hours of
real provider spend. Held in memory until the last one returns, the whole
recording is one process crash, one restart, one Ctrl-C away from having
produced nothing: a live recursion-depth sweep spent seven hours, measured one
cell, and a second process started after it was killed with nothing written at
all. Both recording harnesses in this tree were built that way.

So a cell is appended here the instant it is recorded, and a later run against
the same matrix reads them back instead of paying for them again. The file is
JSON Lines because that is the shape that survives a crash: a process killed
mid-write truncates one line and leaves every earlier line intact, where a
rewritten JSON document loses the lot.

Generic over the record because the two harnesses agree on the mechanism and on
nothing else: they carry different row models, different identity stamps, and
different answers to what makes a row worth reading back. A :class:`JournalSpec`
is where a harness says those three things, and this module is the single owner
of the durability, the header check and the crash tolerance. A second copy of
that logic is how one of them comes to be subtly less durable than the other.
"""

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from io import TextIOWrapper
from pathlib import Path

from pydantic import BaseModel

from evals.errors import HarnessJournalMismatchError, HarnessJournalUnwritableError
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_HARNESS_JOURNAL_RESUMED,
    EVALS_HARNESS_JOURNAL_TRUNCATED,
    EVALS_HARNESS_RECORD_JOURNALLED,
    EVALS_HARNESS_RECORD_REPLAYED,
)

logger = get_logger(__name__)

#: Marks the first line, so a header can never be mistaken for a record.
_HEADER_KIND_FIELD: str = "journal_kind"


@dataclass(frozen=True)
class JournalSpec[RecordT: BaseModel]:
    """What one harness's journal is, beyond the mechanism.

    Attributes:
        kind: Names the journal in its own header, so a file from one harness
            is refused by the other rather than parsed into confusion.
        filename: What the journal is called inside the output directory.
        record_type: The model each line is read back as.
        key_of: The name a record is filed under. The single owner of that
            format for its harness: a resume matches a journalled record to a
            planned one by this string alone, so two spellings of it would
            re-run every cell already paid for.
        resumable: Whether a record is read back rather than attempted again.
            Deliberately not "did it succeed": the question is whether paying
            for it a second time could produce anything better.
    """

    kind: str
    filename: str
    record_type: type[RecordT]
    key_of: Callable[[RecordT], str]
    resumable: Callable[[RecordT], bool]


@dataclass(frozen=True)
class ResumeState[RecordT: BaseModel]:
    """What a previous attempt at this matrix already paid for.

    Two collections rather than one, because they answer different questions
    and the second is NOT a subset view of the first. ``completed`` is what a
    resume reads back instead of re-running. ``recorded`` is every row the
    journal holds, which is what a spend ceiling has to be told about: a cell
    that failed part-way still burned the sessions it burned, that money is
    gone from the account whether or not the cell is attempted again, and a
    ceiling re-armed from the measured rows alone lets a sweep resumed four
    times spend several times what its manifest allowed.

    Attributes:
        completed: Records worth reading back, keyed by the spec's ``key_of``.
        recorded: Every record the journal holds, in the order written.
    """

    completed: Mapping[str, RecordT]
    recorded: tuple[RecordT, ...] = ()

    def holds(self, key: str) -> RecordT | None:
        """The recorded row for *key*, when there is one.

        Returns:
            The row, or ``None`` when this matrix position still has to run.
        """
        return self.completed.get(key)


class RunJournal[RecordT: BaseModel]:
    """Append-only record of every cell a recording finishes."""

    __slots__ = ("_handle", "_spec")

    def __init__(self, handle: TextIOWrapper, spec: JournalSpec[RecordT]) -> None:
        self._handle = handle
        self._spec = spec

    def record(self, record: RecordT) -> None:
        """Append *record* and put it beyond this process's survival.

        Flushed and fsynced rather than left to the OS: the failure this exists
        for is the process dying, and a line sitting in a buffer at that moment
        is a line that was never written.

        Args:
            record: The finished cell, measured or not.
        """
        try:
            self._handle.write(record.model_dump_json() + "\n")
            self._handle.flush()
            os.fsync(self._handle.fileno())
        except (OSError, ValueError) as exc:
            # Raised as its own type rather than as whatever the filesystem
            # said, because a driver's per-cell handler must not treat this as
            # a cell outcome: a journal that cannot be written is true of every
            # remaining cell, and recording an "unavailable" row for it would
            # try to write that row to the same broken file.
            msg = (
                f"the journal could not be written, so this recording can no "
                f"longer keep what it pays for: {exc}"
            )
            raise HarnessJournalUnwritableError(msg) from exc
        logger.info(
            EVALS_HARNESS_RECORD_JOURNALLED,
            journal_kind=self._spec.kind,
            cell=self._spec.key_of(record),
            resumable=self._spec.resumable(record),
        )

    def close(self) -> None:
        """Release the file."""
        self._handle.close()


class RecordedCells[RecordT: BaseModel]:
    """The recording's cells, remembered in memory and written down as they land.

    One owner for both halves. A driver records a cell from several different
    branches, and a journal call beside each of those is one chance per branch
    to add another that only remembers.
    """

    __slots__ = ("_cells", "_journal", "_spec")

    def __init__(
        self, journal: RunJournal[RecordT], spec: JournalSpec[RecordT]
    ) -> None:
        self._journal = journal
        self._spec = spec
        self._cells: list[RecordT] = []

    def add(self, record: RecordT) -> None:
        """Put *record* on disk, then remember it.

        Written FIRST. A journal write that fails leaves the cell in neither
        place rather than in memory alone, so the report this run assembles can
        never claim a cell the journal cannot show. The reverse order is the
        one that produces two disagreeing accounts of the same matrix, and the
        journal is the one an operator reads after a run they could not watch.

        Args:
            record: A cell this recording just finished.
        """
        self._journal.record(record)
        self._cells.append(record)

    def replay(self, record: RecordT) -> None:
        """Remember *record*, which is already on disk.

        Args:
            record: A cell an earlier attempt at this matrix recorded.
        """
        self._cells.append(record)
        logger.info(
            EVALS_HARNESS_RECORD_REPLAYED,
            journal_kind=self._spec.kind,
            cell=self._spec.key_of(record),
        )

    def close(self) -> None:
        """Release the journal file."""
        self._journal.close()

    def __len__(self) -> int:
        """How many cells the recording has.

        Returns:
            The count.
        """
        return len(self._cells)

    @property
    def cells(self) -> tuple[RecordT, ...]:
        """Every cell, in the order it entered the recording.

        Returns:
            The cells.
        """
        return tuple(self._cells)


def _parse[RecordT: BaseModel](line: str, spec: JournalSpec[RecordT]) -> RecordT:
    """Read one journalled record back.

    A record is written with its ``computed_field`` totals, so a killed
    recording's journal reads as a report rather than as raw rows. Those keys
    are derived, and these models forbid extras, so they are dropped before
    validation rather than being kept out of the file: the read is where they
    are known to be redundant, and the file is where somebody looks when a
    recording died.

    Derived from the model rather than listed, because a total added later
    would otherwise make every journal unreadable at the moment it is needed
    most.

    Args:
        line: One journal line.
        spec: What this journal holds.

    Returns:
        The record.
    """
    stamped = json.loads(line)
    for derived in spec.record_type.model_computed_fields:
        stamped.pop(derived, None)
    return spec.record_type.model_validate(stamped)


def _refuse_foreign(
    path: Path, header: str, *, kind: str, identity: Mapping[str, object]
) -> None:
    """Refuse a journal that is not this recording's.

    Args:
        path: The journal, named in the refusal.
        header: Its first line.
        kind: What this journal should be.
        identity: What a resume must agree on.

    Raises:
        HarnessJournalMismatchError: The header is unreadable, belongs to
            another harness, or describes a different matrix.
    """
    try:
        stamped = json.loads(header)
    except json.JSONDecodeError as exc:
        msg = (
            f"the journal at {path} does not start with a readable header, so "
            f"the records under it cannot be attributed to any matrix; move it "
            f"aside to record afresh"
        )
        raise HarnessJournalMismatchError(msg) from exc
    if stamped.get(_HEADER_KIND_FIELD) != kind:
        msg = (
            f"the file at {path} is not a {kind} journal; move it aside "
            f"to record afresh"
        )
        raise HarnessJournalMismatchError(msg)
    differing = sorted(
        field for field, value in identity.items() if stamped.get(field) != value
    )
    if differing:
        msg = (
            f"the journal at {path} was recorded against a different matrix "
            f"({', '.join(differing)} differ), and records from two matrices "
            f"are not one result; move it aside to record afresh"
        )
        raise HarnessJournalMismatchError(msg)


def _recorded[RecordT: BaseModel](
    path: Path, lines: list[str], spec: JournalSpec[RecordT]
) -> list[RecordT]:
    """Parse the record lines, tolerating a crash-truncated last one.

    A partial final line is the signature of the failure this journal exists
    for, so it is dropped with a warning. A partial line anywhere EARLIER is
    corruption rather than a crash, and reading past it would silently drop a
    record the operator already paid for.

    Args:
        path: The journal, named in the refusal.
        lines: Every line after the header.
        spec: What this journal holds.

    Returns:
        The records, in the order they were recorded.

    Raises:
        HarnessJournalMismatchError: A line other than the last is unreadable.
    """
    records: list[RecordT] = []
    for index, line in enumerate(lines):
        try:
            records.append(_parse(line, spec))
        except ValueError as exc:
            if index == len(lines) - 1:
                logger.warning(
                    EVALS_HARNESS_JOURNAL_TRUNCATED,
                    journal=str(path),
                    recorded=len(records),
                )
                break
            msg = (
                f"the journal at {path} is unreadable at record {index + 1} of "
                f"{len(lines)}, which is corruption rather than an interrupted "
                f"write; move it aside to record afresh"
            )
            raise HarnessJournalMismatchError(msg) from exc
    return records


def _lines(path: Path) -> list[str]:
    """Every non-empty line of *path*.

    Returns:
        The lines, in the order they were appended.
    """
    body = path.read_text(encoding="utf-8")
    return [line for line in body.split("\n") if line.strip()]


def read_journal[RecordT: BaseModel](
    path: Path, spec: JournalSpec[RecordT]
) -> tuple[Mapping[str, object], list[RecordT]]:
    """Read a finished journal without opening it for append.

    A re-score reads what a recording already paid for and writes nothing, so
    it must not take the append path: that one creates the file when it is
    absent and would leave a fresh empty journal behind on a typo.

    The header is returned rather than checked against an identity, because a
    re-score has nothing to compare it to. It IS the identity, and it carries
    the RECORDING commit, which the caller must keep rather than stamping
    whatever HEAD happens to be when the report is re-emitted.

    Args:
        path: The journal to read.
        spec: What this journal holds.

    Returns:
        The header's identity fields, with this module's own kind marker
        removed, and every record under it. Stripping the marker here keeps
        the caller from having to know a bookkeeping field it never wrote.

    Raises:
        HarnessJournalMismatchError: The file is missing, empty, holds another
            harness's journal, or is corrupt before its last line.
    """
    if not path.is_file():
        msg = (
            f"there is no journal at {path} to re-score; a re-score reads a "
            f"recording that already happened and cannot create one"
        )
        raise HarnessJournalMismatchError(msg)
    lines = _lines(path)
    if not lines:
        msg = (
            f"the journal at {path} is empty, so its header never reached the "
            f"disk and nothing under it can be attributed"
        )
        raise HarnessJournalMismatchError(msg)
    # Identity deliberately empty: every field would be compared against
    # itself. The kind check still applies, so another harness's journal is
    # refused by name rather than parsed into the wrong record type.
    _refuse_foreign(path, lines[0], kind=spec.kind, identity={})
    stamped: dict[str, object] = json.loads(lines[0])
    stamped.pop(_HEADER_KIND_FIELD, None)
    return stamped, _recorded(path, lines[1:], spec)


def _started[RecordT: BaseModel](
    path: Path, spec: JournalSpec[RecordT], identity: Mapping[str, object]
) -> RunJournal[RecordT]:
    """Open *path* fresh and put its header beyond this process's survival.

    Args:
        path: The journal file.
        spec: What this journal holds.
        identity: What a resume must agree on.

    Returns:
        The open journal.
    """
    handle = path.open("w", encoding="utf-8", newline="")
    handle.write(json.dumps({_HEADER_KIND_FIELD: spec.kind} | dict(identity)) + "\n")
    handle.flush()
    os.fsync(handle.fileno())
    return RunJournal(handle, spec)


def open_journal[RecordT: BaseModel](
    out_dir: Path,
    spec: JournalSpec[RecordT],
    *,
    identity: Mapping[str, object],
    resume: bool,
) -> tuple[RunJournal[RecordT], ResumeState[RecordT]]:
    """Open the journal beside the report, reading back what it already holds.

    Args:
        out_dir: Where the report is written.
        spec: What this journal holds and how it is keyed.
        identity: What a resume must agree on for its records to belong to one
            matrix. Whatever a harness stamps here it must stamp the same way
            on every run, and it must exclude anything that necessarily
            differs between the run that wrote a record and the one reading it
            back.
        resume: Whether an existing journal is read back. ``False`` refuses to
            open onto one rather than truncating it, because the records under
            it are real spend and this is not the place to decide they are
            worthless.

    Returns:
        The open journal and what a previous attempt already paid for.

    Raises:
        HarnessJournalMismatchError: A journal exists that this run must not
            append to.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / spec.filename
    empty: ResumeState[RecordT] = ResumeState(completed={})
    if not path.exists():
        return _started(path, spec, identity), empty
    if not resume:
        msg = (
            f"a journal already exists at {path}; resume to continue that "
            f"recording, or move it aside to record afresh"
        )
        raise HarnessJournalMismatchError(msg)
    lines = _lines(path)
    if not lines:
        # An empty file is a journal whose header never reached the disk, so it
        # attributes nothing. Appending records under no header would make
        # every one of them unreadable at the next resume, which is the failure
        # this whole module exists to prevent, arrived at from the other side.
        return _started(path, spec, identity), empty
    _refuse_foreign(path, lines[0], kind=spec.kind, identity=identity)
    records = _recorded(path, lines[1:], spec)
    state = ResumeState(
        completed={
            spec.key_of(record): record for record in records if spec.resumable(record)
        },
        recorded=tuple(records),
    )
    logger.info(
        EVALS_HARNESS_JOURNAL_RESUMED,
        journal=str(path),
        journal_kind=spec.kind,
        completed=len(state.completed),
        recorded=len(records),
    )
    return RunJournal(path.open("a", encoding="utf-8", newline=""), spec), state


__all__ = [
    "JournalSpec",
    "RecordedCells",
    "ResumeState",
    "RunJournal",
    "open_journal",
    "read_journal",
]
