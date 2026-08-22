# module-kind: tests
"""A cell that was paid for survives the process that produced it."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.errors import RecursionDepthJournalMismatchError
from evals.recursion_depth.journal import (
    JOURNAL_NAME,
    RecordedCells,
    cell_key,
    open_journal,
)
from evals.recursion_depth.manifest import Arm, Independence, ModelPair
from evals.recursion_depth.models import LEAF, CellRecord, Provenance, UnitRecord
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit

_EXECUTOR = ModelPair(
    provider=NotBlankStr("example-provider"),
    model_id=NotBlankStr("example-capable-001"),
    capability="capable",
    family=NotBlankStr("example-family-a"),
)
_REVIEWER = ModelPair(
    provider=NotBlankStr("example-provider"),
    model_id=NotBlankStr("example-expert-001"),
    capability="expert",
    family=NotBlankStr("example-family-b"),
)


def _provenance(*, commit: str = "0" * 40) -> Provenance:
    """Build a provenance stamp.

    Returns:
        The provenance.
    """
    return Provenance(
        generated_at=datetime(2026, 8, 22, tzinfo=UTC),
        git_commit=NotBlankStr(commit),
        git_dirty=False,
        manifest_sha256=NotBlankStr("sha256:" + "0" * 64),
        spec_id=NotBlankStr("tiny"),
        requirement_count=2,
        executor=_EXECUTOR,
        reviewer=_REVIEWER,
        independence=Independence.CROSS_FAMILY,
    )


def _measured(
    *, depth_cap: int = 1, arm: Arm = Arm.GATED, tokens: int = 10
) -> CellRecord:
    """Build a measured cell carrying one leaf.

    Returns:
        The cell.
    """
    return CellRecord(
        depth_cap=depth_cap,
        arm=arm,
        repetition=0,
        achieved_depth=1,
        units=(
            UnitRecord(
                unit_id=NotBlankStr("leaf-1"),
                title=NotBlankStr("A leaf"),
                kind=LEAF,
                depth=1,
                delivered=True,
                attempts=2,
                turns=3,
                tokens=tokens,
            ),
        ),
    )


def _unavailable(*, depth_cap: int = 1, arm: Arm = Arm.UNGATED) -> CellRecord:
    """Build a cell that could not be measured.

    Returns:
        The cell.
    """
    return CellRecord(
        depth_cap=depth_cap,
        arm=arm,
        repetition=0,
        unavailable_reason="DecompositionError: the planner call failed",
    )


class TestACellSurvivesTheProcess:
    """The failure this exists for is the process dying mid-matrix."""

    def test_a_recorded_cell_is_readable_before_the_sweep_ends(
        self, tmp_path: Path
    ) -> None:
        # Not on close, not on the report: a sweep killed after cell one must
        # have cell one. Seven hours of a live run were lost to the opposite.
        journal, _ = open_journal(tmp_path, provenance=_provenance(), resume=False)

        journal.record(_measured())

        lines = (tmp_path / JOURNAL_NAME).read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[1])["depth_cap"] == 1

    def test_the_first_line_is_a_header_not_a_cell(self, tmp_path: Path) -> None:
        open_journal(tmp_path, provenance=_provenance(), resume=False)

        first = json.loads((tmp_path / JOURNAL_NAME).read_text(encoding="utf-8"))
        assert first["kind"] == "recursion-depth-journal"
        assert first["git_commit"] == "0" * 40


class TestResume:
    """What a resume buys again, and what it refuses to."""

    def test_a_measured_cell_is_read_back(self, tmp_path: Path) -> None:
        journal, _ = open_journal(tmp_path, provenance=_provenance(), resume=False)
        journal.record(_measured())
        journal.close()

        _, state = open_journal(tmp_path, provenance=_provenance(), resume=True)

        held = state.holds(cell_key(1, Arm.GATED, 0))
        assert held is not None
        assert held.total_tokens == 10

    def test_an_unavailable_cell_is_attempted_again(self, tmp_path: Path) -> None:
        # It cost almost nothing, and the operator restarting has usually just
        # fixed the reason it failed. Reading it back hands them the same
        # broken report they restarted to escape.
        journal, _ = open_journal(tmp_path, provenance=_provenance(), resume=False)
        journal.record(_unavailable())
        journal.close()

        _, state = open_journal(tmp_path, provenance=_provenance(), resume=True)

        assert state.holds(cell_key(1, Arm.UNGATED, 0)) is None

    def test_the_sessions_a_resumed_cell_spent_are_re_booked(
        self, tmp_path: Path
    ) -> None:
        # Otherwise a sweep resumed four times is bounded like four sweeps, and
        # the ceiling stops meaning what the manifest says it means.
        journal, _ = open_journal(tmp_path, provenance=_provenance(), resume=False)
        journal.record(_measured())
        journal.record(_measured(arm=Arm.UNGATED))
        journal.close()

        _, state = open_journal(tmp_path, provenance=_provenance(), resume=True)

        assert state.sessions_spent == 4

    def test_a_journal_from_a_different_commit_is_refused(self, tmp_path: Path) -> None:
        # Cells measured before a change to the recursion point are cells about
        # a different system, and two of those are not one curve.
        journal, _ = open_journal(tmp_path, provenance=_provenance(), resume=False)
        journal.record(_measured())
        journal.close()

        with pytest.raises(RecursionDepthJournalMismatchError, match="git_commit"):
            open_journal(tmp_path, provenance=_provenance(commit="1" * 40), resume=True)

    def test_an_existing_journal_is_never_silently_overwritten(
        self, tmp_path: Path
    ) -> None:
        # Truncating it would discard hours of paid work, and this is not the
        # place that decision gets made.
        journal, _ = open_journal(tmp_path, provenance=_provenance(), resume=False)
        journal.record(_measured())
        journal.close()

        with pytest.raises(RecursionDepthJournalMismatchError, match="--resume"):
            open_journal(tmp_path, provenance=_provenance(), resume=False)


class TestACrashMidWrite:
    """A truncated last line IS the crash this journal exists for."""

    def test_a_half_written_last_cell_is_dropped_not_fatal(
        self, tmp_path: Path
    ) -> None:
        journal, _ = open_journal(tmp_path, provenance=_provenance(), resume=False)
        journal.record(_measured())
        journal.close()
        path = tmp_path / JOURNAL_NAME
        path.write_text(
            path.read_text(encoding="utf-8") + '{"depth_cap": 2, "ar',
            encoding="utf-8",
            newline="",
        )

        _, state = open_journal(tmp_path, provenance=_provenance(), resume=True)

        assert len(state.completed) == 1

    def test_an_empty_journal_file_is_given_a_header_before_anything_lands(
        self, tmp_path: Path
    ) -> None:
        # A file whose header never reached the disk attributes nothing.
        # Appending under no header makes every cell in it unreadable at the
        # next resume, which is this file's own failure arrived at backwards.
        (tmp_path / JOURNAL_NAME).write_text("", encoding="utf-8", newline="")

        journal, state = open_journal(tmp_path, provenance=_provenance(), resume=True)
        journal.record(_measured())
        journal.close()

        assert not state.completed
        _, resumed = open_journal(tmp_path, provenance=_provenance(), resume=True)
        assert len(resumed.completed) == 1

    def test_a_broken_line_in_the_middle_is_corruption_and_refused(
        self, tmp_path: Path
    ) -> None:
        # Reading past it would silently drop a measured cell already paid for.
        journal, _ = open_journal(tmp_path, provenance=_provenance(), resume=False)
        journal.record(_measured())
        journal.record(_measured(arm=Arm.UNGATED))
        journal.close()
        path = tmp_path / JOURNAL_NAME
        header, first, second = path.read_text(encoding="utf-8").splitlines()
        del first
        path.write_text(
            f"{header}\n{{'not': 'json'}}\n{second}\n", encoding="utf-8", newline=""
        )

        with pytest.raises(RecursionDepthJournalMismatchError, match="corruption"):
            open_journal(tmp_path, provenance=_provenance(), resume=True)


class TestRecordingIsOneOwner:
    """Remembering a cell and writing it down are never separable."""

    def test_adding_a_cell_journals_it(self, tmp_path: Path) -> None:
        # Four branches record a cell. A journal call beside each append is
        # four chances to add a fifth that only remembers.
        journal, _ = open_journal(tmp_path, provenance=_provenance(), resume=False)
        cells = RecordedCells(journal)

        cells.add(_measured())

        assert len(cells) == 1
        written = (tmp_path / JOURNAL_NAME).read_text(encoding="utf-8").splitlines()
        assert len(written) == 2

    def test_a_replayed_cell_is_not_written_twice(self, tmp_path: Path) -> None:
        journal, _ = open_journal(tmp_path, provenance=_provenance(), resume=False)
        journal.record(_measured())
        cells = RecordedCells(journal)

        cells.replay(_measured())

        assert len(cells) == 1
        written = (tmp_path / JOURNAL_NAME).read_text(encoding="utf-8").splitlines()
        assert len(written) == 2
