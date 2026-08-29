# module-kind: tests
"""How a session ended reaches the reader, not just the log.

A merge that assembled nothing and a merge that was stopped before it could
are identical in every other recorded field: both report no delivery, both
carry a rejecting verdict, both show turns and tokens spent. Telling them
apart meant opening the transcripts, and the difference decides whether a flat
curve is a statement about recursion or about the budget the harness set.
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


def _cell_with_merge(terminations: tuple[str, ...]) -> CellRecord:
    """One cap-1 run whose single merge delivered nothing.

    Args:
        terminations: How each assembling session ended.

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
                attempts=len(terminations) * 2,
                turns=79,
                detail="no assembly attempt changed the tree",
                verdict=NotBlankStr("reject"),
                terminations=terminations,
            ),
        ),
        merged_passing=(),
    )


def _markdown(tmp_path: Path, terminations: tuple[str, ...]) -> str:
    """Render the report for a cell whose merge ended *terminations*.

    Returns:
        The Markdown.
    """
    report = assemble_report(
        provenance=_provenance(),
        cells=(_cell_with_merge(terminations),),
        caveats=(ORACLE_CAVEAT,),
        planned_cells=1,
    )
    write_report(report, tmp_path)
    return (tmp_path / REPORT_MARKDOWN_NAME).read_text(encoding="utf-8")


class TestTheEndingReachesTheReader:
    """A stopped run and an idle one must not read alike."""

    def test_every_attempt_is_named_in_order(self, tmp_path: Path) -> None:
        text = _markdown(tmp_path, ("no_op", "budget_exhausted", "budget_exhausted"))

        assert "no_op, budget_exhausted, budget_exhausted" in text

    def test_the_column_is_headed(self, tmp_path: Path) -> None:
        text = _markdown(tmp_path, ("completed",))

        assert "Attempts ended" in text

    def test_a_recording_made_before_the_field_says_so(self, tmp_path: Path) -> None:
        """Silence is stated, because an empty cell reads as a blank column.

        The committed recordings under `results/` carry no terminations and
        stay re-scorable, so the renderer meets the absence rather than
        printing nothing where a reader expects a reason.
        """
        text = _markdown(tmp_path, ())

        assert "not recorded" in text


class TestTheRecordCarriesIt:
    """The journal is what a later question is answered from."""

    def test_a_unit_keeps_the_order_its_sessions_ran_in(self) -> None:
        unit = _cell_with_merge(("no_op", "budget_exhausted")).units[0]

        assert unit.terminations == ("no_op", "budget_exhausted")

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

        assert unit.terminations == ()
