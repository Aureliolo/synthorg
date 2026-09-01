# module-kind: tests
"""Does a cell's report say whether its units agreed on what they shared?

The contract stage exists to move exactly one number, and a number nobody
remembers to take is one the next reader will not have. So it travels on the
record and appears beside the score, rather than living in a script somebody
has to know to run over a work root that ``--keep-workspaces`` may not have
kept.

The pair is rendered rather than the ratio, because the denominator carries a
finding of its own: a cell where no module was written by more than one unit
had nothing to agree about, and a ratio would print that as perfect agreement.
"""

from pathlib import Path

import pytest

from evals.recursion_depth.emit import (
    REPORT_MARKDOWN_NAME,
    assemble_report,
    write_report,
)
from evals.recursion_depth.manifest import Arm
from evals.recursion_depth.models import LEAF, CellRecord, UnitRecord
from synthorg.core.types import NotBlankStr
from tests.evals_spine.recursion_depth.test_emit_gate_table import _provenance

pytestmark = pytest.mark.unit


def _cell(*, shared: int, diverged: int) -> CellRecord:
    """Build one measured cell carrying an agreement reading.

    Returns:
        The cell.
    """
    return CellRecord(
        depth_cap=1,
        arm=Arm.GATED,
        repetition=0,
        achieved_depth=1,
        units=(
            UnitRecord(
                unit_id=NotBlankStr("leaf-1"),
                title=NotBlankStr("Ingest"),
                kind=LEAF,
                depth=1,
                attempts=1,
                cost=1.0,
            ),
        ),
        merged_passing=(),
        shared_modules=shared,
        diverged_modules=diverged,
    )


def _cell_row(text: str) -> list[str]:
    """Take the per-cell table's one data row.

    Returns:
        The row's columns, stripped.
    """
    for line in text.splitlines():
        if line.startswith("| d1-gated-r0 |"):
            return [column.strip() for column in line.strip("|").split("|")]
    msg = "the report has no per-cell row"
    raise AssertionError(msg)


def _rendered(cell: CellRecord, tmp_path: Path) -> str:
    """Emit a report holding *cell*.

    Returns:
        The report markdown.
    """
    report = assemble_report(
        provenance=_provenance(), cells=(cell,), caveats=(), planned_cells=1
    )
    write_report(report, tmp_path)
    return (tmp_path / REPORT_MARKDOWN_NAME).read_text(encoding="utf-8")


class TestTheDivergenceColumn:
    """It has to be readable beside the score, or it is not a covariate."""

    def test_a_cell_whose_units_disagreed_says_so(self, tmp_path: Path) -> None:
        """The corpus's own reading: 11 of 14 shared modules diverged."""
        text = _rendered(_cell(shared=14, diverged=11), tmp_path)

        assert "11/14" in _cell_row(text)

    def test_a_cell_whose_units_agreed_says_so(self, tmp_path: Path) -> None:
        """What the contract stage is supposed to produce."""
        text = _rendered(_cell(shared=9, diverged=0), tmp_path)

        assert "0/9" in _cell_row(text)

    def test_nothing_shared_is_not_reported_as_agreement(self, tmp_path: Path) -> None:
        """Zero of zero would read as a perfect score for an empty measure."""
        text = _rendered(_cell(shared=0, diverged=0), tmp_path)

        assert "none shared" in _cell_row(text)

    def test_the_header_names_the_column(self, tmp_path: Path) -> None:
        text = _rendered(_cell(shared=3, diverged=1), tmp_path)

        assert "Diverged" in text


class TestTheReadingTravelsOnTheRecord:
    """Not on a script, and not on a work root a run may not have kept."""

    def test_the_record_carries_both_halves(self) -> None:
        cell = _cell(shared=14, diverged=11)

        assert (cell.shared_modules, cell.diverged_modules) == (14, 11)

    def test_they_default_to_nothing_measured(self) -> None:
        """A recording made before this existed must still load."""
        cell = CellRecord(
            depth_cap=1,
            arm=Arm.GATED,
            repetition=0,
            achieved_depth=1,
            units=(
                UnitRecord(
                    unit_id=NotBlankStr("leaf-1"),
                    title=NotBlankStr("Ingest"),
                    kind=LEAF,
                    depth=1,
                    attempts=1,
                    cost=1.0,
                ),
            ),
        )

        assert (cell.shared_modules, cell.diverged_modules) == (0, 0)

    def test_a_negative_reading_is_refused(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal to 0"):
            _cell(shared=-1, diverged=0)
