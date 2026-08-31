# module-kind: tests
""" "Spent turns and changed nothing" is a fact readable off the record.

A merge whose four attempts made 167 tool calls and wrote zero files is
identical, in every other recorded field, to one that assembled a real
package: both can show turns, tokens and a rejecting verdict. The workspace
delta is the one field that tells them apart without a transcript.
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
from evals.recursion_depth.unit import files_changed
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
        generated_at=datetime(2026, 8, 28, tzinfo=UTC),
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


def _cell_with_merge(workspace_files_changed: int | None) -> CellRecord:
    """One cap-1 run whose single merge carries *workspace_files_changed*.

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
                depth=0,
                delivered=False,
                attempts=2,
                turns=79,
                detail="no assembly attempt changed the tree",
                verdict=NotBlankStr("reject"),
                workspace_files_changed=workspace_files_changed,
            ),
        ),
        merged_passing=(),
    )


def _markdown(tmp_path: Path, workspace_files_changed: int | None) -> str:
    """Render the report for a cell whose merge carries *workspace_files_changed*.

    Returns:
        The Markdown.
    """
    report = assemble_report(
        provenance=_provenance(),
        cells=(_cell_with_merge(workspace_files_changed),),
        caveats=(ORACLE_CAVEAT,),
        planned_cells=1,
    )
    write_report(report, tmp_path)
    return (tmp_path / REPORT_MARKDOWN_NAME).read_text(encoding="utf-8")


class TestFilesChanged:
    """The pure primitive: the symmetric difference of two fingerprints."""

    def test_an_unchanged_tree_counts_zero(self) -> None:
        before = frozenset({("a.py", "digest-1")})

        assert files_changed(before, before) == 0

    def test_an_added_file_counts_once(self) -> None:
        before = frozenset({("a.py", "digest-1")})
        after = before | {("b.py", "digest-2")}

        assert files_changed(before, after) == 1

    def test_a_removed_file_counts_once(self) -> None:
        before = frozenset({("a.py", "digest-1"), ("b.py", "digest-2")})
        after = frozenset({("a.py", "digest-1")})

        assert files_changed(before, after) == 1

    def test_an_edited_file_counts_twice(self) -> None:
        """Its old pair drops out and its new one enters: two entries, one file."""
        before = frozenset({("a.py", "digest-1")})
        after = frozenset({("a.py", "digest-2")})

        assert files_changed(before, after) == 2


class TestTheEndingReachesTheReader:
    """Readable from the report without opening a transcript."""

    def test_a_run_that_changed_nothing_is_zero_in_the_table(
        self, tmp_path: Path
    ) -> None:
        text = _markdown(tmp_path, 0)

        assert "| 0 |" in text

    def test_the_column_is_headed(self, tmp_path: Path) -> None:
        text = _markdown(tmp_path, 3)

        assert "Files changed" in text

    def test_a_recording_made_before_the_field_existed_says_so(
        self, tmp_path: Path
    ) -> None:
        """Distinct from ``0``: the earlier journal never asked the question."""
        text = _markdown(tmp_path, None)

        assert "not recorded" in text


class TestTheRecordCarriesIt:
    """The journal is what a later question is answered from."""

    def test_a_unit_keeps_the_count_it_was_given(self) -> None:
        unit = _cell_with_merge(5).units[0]

        assert unit.workspace_files_changed == 5

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

        assert unit.workspace_files_changed is None
