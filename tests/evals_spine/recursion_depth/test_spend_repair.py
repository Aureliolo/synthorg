# module-kind: tests
"""Rebuilding a scrambled spend column from the recorder's own per-call log.

The fault the repair undoes: ``open_run_ledger`` installs a session's cost sink
as a process-wide field, so concurrent leaves swapped over each other and some
sessions collected nothing while others absorbed their neighbours' records. The
attribution problem is the interesting half, because the root merge's task id is
derived from the specification and therefore repeats across every cell.
"""

from pathlib import Path

import pytest

from evals.recursion_depth.manifest import Arm
from evals.recursion_depth.models import LEAF, MERGE, CellRecord, UnitRecord
from evals.recursion_depth.spend_repair import repair_cell_spend, tokens_by_unit
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit

#: One id, shared by every cell's root assembly, exactly as the real harness
#: mints it. The whole reason a task id alone cannot attribute spend.
_SHARED_ROOT = "45099520-df94-5de7-8402-f5a2ced2986f"


def _cost_line(task: str, *, tokens: int, productive: bool = True) -> str:
    """One ``cost.recorded`` line, coloured the way the recorder writes it.

    Returns:
        The log line.
    """
    category = "productive" if productive else "system"
    # Colour codes between key and value, which is what defeats a naive match.
    return (
        f"cost.recorded \x1b[36mcall_category\x1b[0m=\x1b[35m{category}\x1b[0m "
        f"\x1b[36minput_tokens\x1b[0m=\x1b[35m{tokens}\x1b[0m "
        f"\x1b[36moutput_tokens\x1b[0m=\x1b[35m0\x1b[0m "
        f"\x1b[36mtask_id\x1b[0m=\x1b[35m{task}\x1b[0m"
    )


def _journalled(cell: str, unit: str) -> str:
    """One ``record_journalled`` line.

    Returns:
        The log line.
    """
    return f"record_journalled \x1b[36mcell\x1b[0m=\x1b[35m{cell}/{unit}\x1b[0m"


class TestAttribution:
    """A task id is not enough, and the interval is what fixes it."""

    def test_a_shared_root_id_is_split_between_its_cells(self, tmp_path: Path) -> None:
        # The defect this exists for: joining on the task id alone gave every
        # cell the sum of all of them, and reported the ledger understating by
        # 78.9% when the true figure was 24.9%.
        log = tmp_path / "run.log"
        log.write_text(
            "\n".join(
                [
                    _cost_line(_SHARED_ROOT, tokens=100),
                    _journalled("d1-gated-r0", _SHARED_ROOT),
                    _cost_line(_SHARED_ROOT, tokens=700),
                    _journalled("d2-gated-r0", _SHARED_ROOT),
                ]
            ),
            encoding="utf-8",
        )

        attributed = tokens_by_unit(log)

        assert attributed[("d1-gated-r0", _SHARED_ROOT)] == 100
        assert attributed[("d2-gated-r0", _SHARED_ROOT)] == 700

    def test_a_non_productive_call_is_not_counted(self, tmp_path: Path) -> None:
        log = tmp_path / "run.log"
        log.write_text(
            "\n".join(
                [
                    _cost_line("leaf-a", tokens=50),
                    _cost_line("leaf-a", tokens=900, productive=False),
                    _journalled("d1-gated-r0", "leaf-a"),
                ]
            ),
            encoding="utf-8",
        )

        assert tokens_by_unit(log)[("d1-gated-r0", "leaf-a")] == 50

    def test_calls_no_unit_claimed_are_reported(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A log captured while the run was still appending. Silence here would
        # be the same class of loss the repair undoes.
        log = tmp_path / "run.log"
        log.write_text(_cost_line("orphan", tokens=42), encoding="utf-8")

        tokens_by_unit(log)

        assert "spend_unclaimed" in caplog.text


class TestApplyingIt:
    """What gets overwritten, and what must not be."""

    def test_a_zeroed_leaf_is_restored(self) -> None:
        cell = CellRecord(
            depth_cap=1,
            arm=Arm.GATED,
            repetition=0,
            achieved_depth=1,
            units=(
                UnitRecord(
                    unit_id=NotBlankStr("leaf-a"),
                    title=NotBlankStr("a"),
                    kind=LEAF,
                    depth=0,
                    tokens=0,
                ),
            ),
        )

        repaired = repair_cell_spend([cell], {("d1-gated-r0", "leaf-a"): 12_345})

        assert repaired[0].units[0].tokens == 12_345

    def test_a_unit_the_log_cannot_see_keeps_its_figure(self) -> None:
        # The plan unit. Planning is not a task, so it carries no id the log
        # knows, and it ran sequentially on its own tracker, which is the one
        # kind of session the race never touched. Overwriting it with a zero
        # would erase a figure that was always right.
        cell = CellRecord(
            depth_cap=1,
            arm=Arm.GATED,
            repetition=0,
            achieved_depth=1,
            units=(
                UnitRecord(
                    unit_id=NotBlankStr("d1-gated-r0-plan"),
                    title=NotBlankStr("plan"),
                    kind=MERGE,
                    depth=0,
                    tokens=24_690,
                ),
            ),
        )

        repaired = repair_cell_spend([cell], {})

        assert repaired[0].units[0].tokens == 24_690
