# module-kind: tests
"""The gate table's escalation count survives a merge that later recovered.

`UnitRecord.parked` reads only the LAST review, so a merge that parked on
attempt 1 and was approved on attempt 2 reads ``parked=False`` -- correctly,
it was judged in the end. But an escalation genuinely happened, and
`parked_attempts` is the field that says so. Summing `int(unit.parked)`
in `_gate_table` would undercount every merge in this shape to zero.
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
from evals.recursion_depth.models import MERGE, CellRecord, Provenance, UnitRecord
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit


def _provenance() -> Provenance:
    """What a recording is measured against.

    Returns:
        The provenance.
    """
    return Provenance(
        generated_at=datetime(2026, 8, 31, tzinfo=UTC),
        git_commit=NotBlankStr("0" * 40),
        git_dirty=False,
        manifest_sha256=NotBlankStr("sha256:" + "0" * 64),
        spec_id=NotBlankStr("sqlcsv"),
        requirement_count=2,
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


def _recovered_merge_cell() -> CellRecord:
    """A gated cell whose merge parked once, then was approved.

    Returns:
        The cell.
    """
    unit = UnitRecord(
        unit_id=NotBlankStr("merge-1"),
        title=NotBlankStr("Assemble it"),
        kind=MERGE,
        depth=0,
        attempts=4,
        cost=3.0,
        tokens=1000,
        parked=False,
        parked_attempts=1,
        terminations=("completed", "completed"),
    )
    return CellRecord(
        depth_cap=2,
        arm=Arm.GATED,
        repetition=0,
        achieved_depth=2,
        units=(unit,),
        merged_passing=(),
    )


def _gate_table_row(text: str, arm: str) -> str:
    """The gate table's row for *arm*.

    Returns:
        The row.
    """
    return next(line for line in text.splitlines() if line.startswith(f"| {arm} |"))


class TestParkedEscalationsSurviveRecovery:
    def test_a_merge_parked_then_approved_still_counts_as_one_escalation(
        self, tmp_path: Path
    ) -> None:
        report = assemble_report(
            provenance=_provenance(),
            cells=(_recovered_merge_cell(),),
            caveats=(),
            planned_cells=1,
        )
        write_report(report, tmp_path)
        text = (tmp_path / REPORT_MARKDOWN_NAME).read_text(encoding="utf-8")

        row = _gate_table_row(text, "gated")
        columns = [c.strip() for c in row.strip("|").split("|")]
        # Columns: Arm, Merges, Sessions, Tokens, Judging, Spend,
        # Parked escalations, Contract amendments.
        assert columns[6] == "1"
