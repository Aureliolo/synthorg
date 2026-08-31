# module-kind: tests
"""A cell whose gate rendered no verdict is a missing observation, not a
gated one.

`BlindMergeReviewer` (the ungated control) returns ``approved=None`` on every
attempt by design, so a predicate keyed on verdict absence would erase the
control arm from the curve along with the cells it is actually meant to
exclude. Keyed on PARK EXHAUSTION instead, which is structurally impossible
in the ungated arm.
"""

from datetime import UTC, datetime

import pytest

from evals.recursion_depth.claims import RequirementId
from evals.recursion_depth.emit import assemble_report
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

_REQUIRED = 2


def _provenance() -> Provenance:
    """What a recording is measured against.

    Returns:
        The provenance.
    """
    return Provenance(
        generated_at=datetime(2026, 8, 30, tzinfo=UTC),
        git_commit=NotBlankStr("0123456789abcdef0123456789abcdef01234567"),
        git_dirty=False,
        manifest_sha256=NotBlankStr("sha256:" + "0" * 64),
        spec_id=NotBlankStr("sqlcsv"),
        requirement_count=_REQUIRED,
        executor=ModelPair(
            provider=NotBlankStr("example-provider"),
            model_id=NotBlankStr("example-capable-001"),
            capability="capable",
        ),
        reviewer=ModelPair(
            provider=NotBlankStr("example-provider"),
            model_id=NotBlankStr("example-expert-001"),
            capability="expert",
        ),
        independence=Independence.SAME_FAMILY,
    )


def _merge(
    *, parked_attempts: int, terminations: tuple[str, ...], cost: float = 1.0
) -> UnitRecord:
    """One merge unit, judged or park-exhausted depending on its counts.

    Returns:
        The unit.
    """
    return UnitRecord(
        unit_id=NotBlankStr("merge-1"),
        title=NotBlankStr("Assemble it"),
        kind=MERGE,
        depth=0,
        attempts=len(terminations) * 2,
        cost=cost,
        tokens=1000,
        parked_attempts=parked_attempts,
        terminations=terminations,
    )


def _judged_gated_cell() -> CellRecord:
    """A gated cell whose merge reached a real verdict.

    Returns:
        The cell.
    """
    return CellRecord(
        depth_cap=2,
        arm=Arm.GATED,
        repetition=0,
        achieved_depth=2,
        units=(_merge(parked_attempts=0, terminations=("completed",), cost=2.0),),
        merged_passing=(RequirementId("R01"),),
    )


def _unjudged_gated_cell() -> CellRecord:
    """A gated cell whose merge exhausted every round on a park.

    Returns:
        The cell.
    """
    return CellRecord(
        depth_cap=2,
        arm=Arm.GATED,
        repetition=1,
        achieved_depth=2,
        units=(
            _merge(
                parked_attempts=2,
                terminations=("completed", "completed"),
                cost=3.0,
            ),
        ),
        merged_passing=(RequirementId("R01"), RequirementId("R02")),
    )


def _ungated_cell() -> CellRecord:
    """The control: a merge that ran, self-reviewed, and never parked.

    Returns:
        The cell.
    """
    return CellRecord(
        depth_cap=2,
        arm=Arm.UNGATED,
        repetition=0,
        achieved_depth=2,
        units=(_merge(parked_attempts=0, terminations=("completed",), cost=1.5),),
        merged_passing=(RequirementId("R01"),),
    )


class TestUnjudgedCellsAreExcludedFromTheCurveAlone:
    def test_the_curve_bins_the_judged_and_ungated_cells(self) -> None:
        report = assemble_report(
            provenance=_provenance(),
            cells=(_judged_gated_cell(), _unjudged_gated_cell(), _ungated_cell()),
            caveats=(ORACLE_CAVEAT,),
            planned_cells=3,
        )

        counted_cells = sum(point.cells for point in report.by_achieved_depth)
        # Two of the three: the judged gated cell and the ungated control.
        assert counted_cells == 2
        arms = {point.arm for point in report.by_achieved_depth}
        assert Arm.GATED in arms
        assert Arm.UNGATED in arms

    def test_the_ungated_control_is_never_read_as_unjudged(self) -> None:
        """The load-bearing case: a predicate keyed on verdict absence would
        erase this cell too, since the control never renders a verdict."""
        assert _ungated_cell().is_unjudged is False

    def test_unjudged_by_depth_names_the_excluded_cell(self) -> None:
        report = assemble_report(
            provenance=_provenance(),
            cells=(_judged_gated_cell(), _unjudged_gated_cell(), _ungated_cell()),
            caveats=(),
            planned_cells=3,
        )

        assert report.unjudged_by_depth == {"2": 1}

    def test_all_three_cells_keep_their_spend_in_the_report(self) -> None:
        report = assemble_report(
            provenance=_provenance(),
            cells=(_judged_gated_cell(), _unjudged_gated_cell(), _ungated_cell()),
            caveats=(),
            planned_cells=3,
        )

        assert len(report.cells) == 3
        costs = sorted(
            cost for cell in report.cells if (cost := cell.total_cost) is not None
        )
        assert costs == pytest.approx([1.5, 2.0, 3.0])

    def test_survival_and_spread_still_include_the_unjudged_cell(self) -> None:
        """The gate never touches the held-out oracle's own grading, so a
        park does not corrupt what the oracle already measured."""
        report = assemble_report(
            provenance=_provenance(),
            cells=(_judged_gated_cell(), _unjudged_gated_cell(), _ungated_cell()),
            caveats=(),
            planned_cells=3,
        )

        counted = sum(point.cells for point in report.spread_by_achieved_depth)
        assert counted == 3

    def test_a_fully_judged_sweep_reports_nothing_excluded(self) -> None:
        report = assemble_report(
            provenance=_provenance(),
            cells=(_judged_gated_cell(), _ungated_cell()),
            caveats=(),
            planned_cells=2,
        )

        assert report.unjudged_by_depth == {}
