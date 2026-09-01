# module-kind: tests
"""Whether the scored tree was verified is readable beside the score.

``oracle.py`` grades behaviour with its own held-out tests and never reads the
unit's, so a merge that carried its pieces' tests up and one that discarded them
score alike. Measured on a live cap-1 smoke: 40 against 39, on trees holding
thirteen test files and zero. ``delivered`` is one bit and cannot separate
"carried twelve up and two fail" from "carried none", so the count is what makes
the published number readable.

The column belongs beside ``Satisfied`` in the per-cell table rather than only in
the per-merge table further down, because it qualifies the headline figure a
depth curve is read for.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.recursion_depth.emit import (
    REPORT_MARKDOWN_NAME,
    assemble_report,
    write_report,
)
from evals.recursion_depth.manifest import Arm, Independence, ModelPair
from evals.recursion_depth.models import (
    MERGE,
    ORACLE_CAVEAT,
    CellRecord,
    Provenance,
    UnitRecord,
)
from evals.recursion_depth.unit import count_test_files
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit

_REQUIRED = 4


def _provenance() -> Provenance:
    """The sweep-level stamp every report carries.

    Returns:
        The provenance.
    """
    return Provenance(
        git_commit=NotBlankStr("0" * 40),
        git_dirty=False,
        generated_at=datetime(2026, 9, 1, tzinfo=UTC),
        manifest_sha256=NotBlankStr("sha256:" + "0" * 64),
        spec_id=NotBlankStr("sqlcsv"),
        requirement_count=_REQUIRED,
        executor=ModelPair(
            provider=NotBlankStr("example-provider"),
            model_id=NotBlankStr("example-capable-001"),
            capability="capable",
            family=NotBlankStr("example-family-a"),
        ),
        reviewer=ModelPair(
            provider=NotBlankStr("example-provider"),
            model_id=NotBlankStr("example-expert-001"),
            capability="expert",
            family=NotBlankStr("example-family-b"),
        ),
        independence=Independence.CROSS_FAMILY,
    )


def _cell(test_files: int | None, *, depth: int = 0) -> CellRecord:
    """One cap-1 run whose single merge carries *test_files*.

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
                unit_id=NotBlankStr("merge-root"),
                title=NotBlankStr("Assemble: a thing"),
                kind=MERGE,
                depth=depth,
                delivered=False,
                attempts=2,
                turns=79,
                detail="the merged tree's own tests did not pass",
                verdict=NotBlankStr("reject"),
                test_files=test_files,
            ),
        ),
        merged_passing=(),
    )


def _markdown(tmp_path: Path, cell: CellRecord) -> str:
    """Render the report for *cell*.

    Returns:
        The Markdown.
    """
    report = assemble_report(
        provenance=_provenance(),
        cells=(cell,),
        caveats=(ORACLE_CAVEAT,),
        planned_cells=1,
    )
    write_report(report, tmp_path)
    return (tmp_path / REPORT_MARKDOWN_NAME).read_text(encoding="utf-8")


class TestCountingTheTreesOwnTests:
    """The pure primitive over a produced-tree fingerprint."""

    def test_a_tree_with_no_tests_counts_zero(self) -> None:
        tree = frozenset({("sqlcsv/parser.py", "digest-1")})

        assert count_test_files(tree) == 0

    def test_it_counts_the_test_files(self) -> None:
        tree = frozenset(
            {
                ("sqlcsv/parser.py", "digest-1"),
                ("tests/test_parser.py", "digest-2"),
                ("tests/test_lexer.py", "digest-3"),
            }
        )

        assert count_test_files(tree) == 2

    def test_where_a_unit_puts_its_suite_is_its_own_decision(self) -> None:
        """Matched on the basename: every cell measured chose a different layout."""
        tree = frozenset(
            {
                ("test_at_the_root.py", "digest-1"),
                ("suite/nested/test_deep.py", "digest-2"),
            }
        )

        assert count_test_files(tree) == 2

    def test_a_conftest_is_not_a_test_file(self) -> None:
        """It carries fixtures; counting it would overstate what was verified."""
        tree = frozenset({("tests/conftest.py", "digest-1")})

        assert count_test_files(tree) == 0

    def test_a_module_merely_starting_with_test_is_not_one(self) -> None:
        """``test_`` is the pytest prefix; ``testing.py`` is a helper module."""
        tree = frozenset({("sqlcsv/testing.py", "digest-1")})

        assert count_test_files(tree) == 0

    def test_a_non_python_file_is_not_one(self) -> None:
        tree = frozenset({("tests/test_cases.json", "digest-1")})

        assert count_test_files(tree) == 0


class TestTheCountReachesTheReader:
    """Beside the score, which is the whole point of the field."""

    def test_a_tree_scored_without_any_tests_reads_zero(self, tmp_path: Path) -> None:
        """The shape that scored 39 of 42 on a live cell."""
        text = _markdown(tmp_path, _cell(0))

        assert "| 0 |" in text

    def test_the_column_is_headed(self, tmp_path: Path) -> None:
        text = _markdown(tmp_path, _cell(13))

        assert "Test files" in text

    def test_it_sits_in_the_per_cell_table_beside_satisfied(
        self, tmp_path: Path
    ) -> None:
        """Not only in the per-merge table: the curve is what a reader reads."""
        text = _markdown(tmp_path, _cell(13))
        header = next(
            line for line in text.splitlines() if "Satisfied" in line and "Cell" in line
        )

        assert "Test files" in header

    def test_a_recording_made_before_the_field_existed_says_so(
        self, tmp_path: Path
    ) -> None:
        """Distinct from ``0``: the earlier journal never asked the question."""
        text = _markdown(tmp_path, _cell(None))

        assert "not recorded" in text


class TestTheRecordCarriesIt:
    """The journal is what a later question is answered from."""

    def test_a_unit_keeps_the_count_it_was_given(self) -> None:
        unit = _cell(13).units[0]

        assert unit.test_files == 13

    def test_an_older_record_defaults_to_none_recorded(self) -> None:
        """A journal written before the field existed still loads."""
        unit = UnitRecord.model_validate(
            {
                "unit_id": "merge-root",
                "title": "Assemble: a thing",
                "kind": MERGE,
                "depth": 0,
            }
        )

        assert unit.test_files is None

    def test_a_negative_count_is_refused(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal to 0"):
            UnitRecord(
                unit_id=NotBlankStr("merge-root"),
                title=NotBlankStr("Assemble: a thing"),
                kind=MERGE,
                depth=0,
                test_files=-1,
            )
