# module-kind: tests
"""The artefacts carry the variance three repetitions were paid for.

A pooled curve is a mean of draws nobody can count from the chart, so a matrix
recorded three times per depth reports the same number as one recorded once. The
spread table and the per-cell table are what make the population visible, and
they have to survive the round trip a re-score takes, because a re-score is how
a scoring change reaches a recording that already cost money.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.recursion_depth.claims import RequirementId
from evals.recursion_depth.emit import (
    REPORT_CHART_NAME,
    REPORT_MARKDOWN_NAME,
    assemble_report,
    load_report,
    write_report,
)
from evals.recursion_depth.manifest import Arm, Independence, ModelPair
from evals.recursion_depth.models import (
    LEAF,
    ORACLE_CAVEAT,
    CellRecord,
    Provenance,
    UnitRecord,
)
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit

#: Small so a fraction reads at a glance; the real specification carries 42.
_REQUIRED = 4


def _provenance() -> Provenance:
    """What a recording is measured against.

    Returns:
        The provenance.
    """
    return Provenance(
        generated_at=datetime(2026, 8, 27, tzinfo=UTC),
        git_commit=NotBlankStr("0123456789abcdef0123456789abcdef01234567"),
        git_dirty=False,
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


def _cell(*, repetition: int, passing: tuple[str, ...]) -> CellRecord:
    """One measured run of cap 3, claiming two requirements through one leaf.

    Returns:
        The cell.
    """
    return CellRecord(
        depth_cap=3,
        arm=Arm.GATED,
        repetition=repetition,
        achieved_depth=3,
        units=(
            UnitRecord(
                unit_id=NotBlankStr(f"leaf-{repetition}"),
                title=NotBlankStr("build it"),
                kind=LEAF,
                depth=2,
                claimed=(RequirementId("R01"), RequirementId("R02")),
                delivered=True,
                attempts=2,
                tokens=1000,
                cost=0.5,
            ),
        ),
        merged_passing=tuple(RequirementId(item) for item in passing),
    )


def _three_plus_one_unavailable() -> tuple[CellRecord, ...]:
    """Three runs that disagree, and one that never measured anything.

    Returns:
        The cells.
    """
    return (
        _cell(repetition=0, passing=("R01",)),
        _cell(repetition=1, passing=("R01", "R02")),
        _cell(repetition=2, passing=("R01", "R02", "R03", "R04")),
        CellRecord(
            depth_cap=4,
            arm=Arm.GATED,
            repetition=0,
            unavailable_reason="ProviderQuotaExceededError: the account is dry",
        ),
    )


def _markdown(tmp_path: Path) -> str:
    """Write the report and read its Markdown back.

    Returns:
        The rendered Markdown.
    """
    report = assemble_report(
        provenance=_provenance(),
        cells=_three_plus_one_unavailable(),
        caveats=(ORACLE_CAVEAT,),
        planned_cells=4,
    )
    write_report(report, tmp_path)
    return (tmp_path / REPORT_MARKDOWN_NAME).read_text(encoding="utf-8")


class TestTheSpreadReachesTheReader:
    """A range hidden inside `cells` is one nobody computes."""

    def test_the_markdown_carries_a_spread_table(self, tmp_path: Path) -> None:
        text = _markdown(tmp_path)

        assert "## Per-depth spread" in text
        assert "Satisfied (min..max)" in text

    def test_the_range_is_the_one_the_runs_recorded(self, tmp_path: Path) -> None:
        text = _markdown(tmp_path)

        assert "| 1..4 |" in text

    def test_an_unmeasured_survival_reads_absent_rather_than_zero(self) -> None:
        # Every run here attributed work, so the rate is present. The absent
        # rendering is exercised where it can actually occur: a bucket whose
        # delivered leaves claimed nothing.
        nothing = CellRecord(
            depth_cap=3,
            arm=Arm.GATED,
            repetition=0,
            achieved_depth=3,
            units=(
                UnitRecord(
                    unit_id=NotBlankStr("leaf-x"),
                    title=NotBlankStr("build it"),
                    kind=LEAF,
                    depth=2,
                    claimed=(RequirementId("R01"),),
                    delivered=False,
                    attempts=1,
                ),
            ),
        )

        report = assemble_report(
            provenance=_provenance(),
            cells=(nothing,),
            caveats=(),
            planned_cells=1,
        )

        assert report.spread_by_achieved_depth[0].survival_min is None


class TestEveryCellIsListed:
    """The population behind every figure, including what it cost to fail."""

    def test_one_row_per_recorded_run(self, tmp_path: Path) -> None:
        text = _markdown(tmp_path)

        for key in ("d3-gated-r0", "d3-gated-r1", "d3-gated-r2", "d4-gated-r0"):
            assert f"| {key} |" in text

    def test_an_unavailable_cell_is_listed_rather_than_dropped(
        self, tmp_path: Path
    ) -> None:
        # It cost real money, and leaving it out makes the matrix read as
        # smaller than the one that was paid for.
        text = _markdown(tmp_path)

        assert "| d4-gated-r0 | unavailable |" in text


class TestASingleArmChart:
    """A legend naming a line nobody drew reads as an arm that scored nothing."""

    def test_only_the_arm_that_ran_is_keyed(self, tmp_path: Path) -> None:
        report = assemble_report(
            provenance=_provenance(),
            cells=_three_plus_one_unavailable(),
            caveats=(),
            planned_cells=4,
        )
        write_report(report, tmp_path)

        svg = (tmp_path / REPORT_CHART_NAME).read_text(encoding="utf-8")

        assert "gate at every merge" in svg
        assert "no merge gate" not in svg


class TestTheRoundTripARescoreTakes:
    """A report that cannot be read back is one a scoring fix cannot reach."""

    def test_a_report_carrying_spread_loads(self, tmp_path: Path) -> None:
        report = assemble_report(
            provenance=_provenance(),
            cells=_three_plus_one_unavailable(),
            caveats=(ORACLE_CAVEAT,),
            planned_cells=4,
        )
        json_path, _, _ = write_report(report, tmp_path)

        loaded = load_report(json_path)

        assert loaded.spread_by_achieved_depth == report.spread_by_achieved_depth
        assert loaded.spread_by_depth_cap == report.spread_by_depth_cap
