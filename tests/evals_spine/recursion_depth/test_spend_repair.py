# module-kind: tests
"""Rebuilding a scrambled spend column from the recorder's own per-call log.

The fault the repair undoes: ``open_run_ledger`` installs a session's cost sink
as a process-wide field, so concurrent leaves swapped over each other and some
sessions collected nothing while others absorbed their neighbours' records. The
attribution problem is the interesting half, because the root merge's task id is
derived from the specification and therefore repeats across every cell.

Ids here are hex UUIDs because that is what the recorder writes and what the
parser matches; a readable label would be quietly unparseable and every
assertion below would pass against an empty result.
"""

from pathlib import Path

import pytest
import structlog

from evals.recursion_depth.manifest import Arm
from evals.recursion_depth.models import LEAF, MERGE, PLAN, CellRecord, UnitRecord
from evals.recursion_depth.spend_repair import repair_cell_spend, tokens_by_unit
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit

#: One id, shared by every cell's root assembly and by the planning that wrote
#: the tree, exactly as the real harness mints it. The whole reason a task id
#: alone cannot attribute spend.
_SHARED_ROOT = "45099520-df94-5de7-8402-f5a2ced2986f"

_LEAF_A = "a1b2c3d4-0000-4000-8000-000000000001"
_LEAF_B = "a1b2c3d4-0000-4000-8000-000000000002"


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
    """One ``record_journalled`` line for a unit row.

    Returns:
        The log line.
    """
    return f"record_journalled \x1b[36mcell\x1b[0m=\x1b[35m{cell}/{unit}\x1b[0m"


def _cell_journalled(cell: str) -> str:
    """One ``record_journalled`` line for the whole-cell row.

    The same event, keyed on the cell alone, written once a cell's units are
    all recorded.

    Returns:
        The log line.
    """
    return (
        f"record_journalled \x1b[36mcell\x1b[0m=\x1b[35m{cell}\x1b[0m "
        f"\x1b[36mresumable\x1b[0m=\x1b[35mTrue\x1b[0m"
    )


def _log(tmp_path: Path, *lines: str) -> Path:
    """Write *lines* as a recorder log.

    Returns:
        Where it was written.
    """
    path = tmp_path / "run.log"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _unit(unit_id: str, *, kind: str, tokens: int) -> UnitRecord:
    """One journalled unit carrying *tokens*.

    Returns:
        The record.
    """
    return UnitRecord(
        unit_id=NotBlankStr(unit_id),
        title=NotBlankStr(unit_id),
        kind=kind,  # type: ignore[arg-type]
        depth=0,
        tokens=tokens,
    )


def _cell(*units: UnitRecord) -> CellRecord:
    """A cap-1 gated cell holding *units*.

    Returns:
        The record, keyed ``d1-gated-r0``.
    """
    return CellRecord(
        depth_cap=1,
        arm=Arm.GATED,
        repetition=0,
        achieved_depth=1,
        units=units,
    )


class TestAttribution:
    """A task id is not enough, and the interval is what fixes it."""

    def test_a_shared_root_id_is_split_between_its_cells(self, tmp_path: Path) -> None:
        # The defect this exists for: joining on the task id alone gave every
        # cell the sum of all of them, and reported the ledger understating by
        # 78.9% when the true figure was 24.9%.
        log = _log(
            tmp_path,
            _cost_line(_SHARED_ROOT, tokens=100),
            _journalled("d1-gated-r0", _SHARED_ROOT),
            _cost_line(_SHARED_ROOT, tokens=700),
            _journalled("d2-gated-r0", _SHARED_ROOT),
        )

        attributed = tokens_by_unit(log)

        assert attributed[("d1-gated-r0", _SHARED_ROOT)] == 100
        assert attributed[("d2-gated-r0", _SHARED_ROOT)] == 700

    def test_a_non_productive_call_is_not_counted(self, tmp_path: Path) -> None:
        log = _log(
            tmp_path,
            _cost_line(_LEAF_A, tokens=50),
            _cost_line(_LEAF_A, tokens=900, productive=False),
            _journalled("d1-gated-r0", _LEAF_A),
        )

        assert tokens_by_unit(log)[("d1-gated-r0", _LEAF_A)] == 50

    def test_a_unit_with_no_call_of_its_own_gets_no_entry(self, tmp_path: Path) -> None:
        # An entry reads as a measured zero, and repair_cell_spend has no way to
        # tell one from a figure it should replace.
        log = _log(
            tmp_path,
            _cost_line(_LEAF_A, tokens=50),
            _journalled("d1-gated-r0", _LEAF_A),
            _journalled("d1-gated-r0", _LEAF_B),
        )

        assert ("d1-gated-r0", _LEAF_B) not in tokens_by_unit(log)

    def test_planning_is_cut_off_before_the_merge_that_shares_its_id(
        self, tmp_path: Path
    ) -> None:
        # Planning dispatches under the root task id and the root merge reuses
        # it, while the plan's own journalled id is minted. Without a cut at the
        # plan row the whole tree's planning spend lands on that merge.
        log = _log(
            tmp_path,
            _cost_line(_SHARED_ROOT, tokens=300),
            _journalled("d1-gated-r0", "d1-gated-r0-plan"),
            _cost_line(_SHARED_ROOT, tokens=40),
            _journalled("d1-gated-r0", _SHARED_ROOT),
        )

        attributed = tokens_by_unit(log)

        assert attributed[("d1-gated-r0", "d1-gated-r0-plan")] == 300
        assert attributed[("d1-gated-r0", _SHARED_ROOT)] == 40

    def test_calls_no_unit_claimed_are_reported(self, tmp_path: Path) -> None:
        # A log captured while the run was still appending. Silence here would
        # be the same class of loss the repair undoes.
        log = _log(tmp_path, _cost_line(_LEAF_A, tokens=42))

        with structlog.testing.capture_logs() as logs:
            tokens_by_unit(log)

        unclaimed = [
            entry
            for entry in logs
            if entry["event"] == "evals.recursion_depth.spend_unclaimed"
        ]
        assert unclaimed[0]["unclaimed_tasks"] == 1
        assert unclaimed[0]["unclaimed_tokens"] == 42

    def test_the_whole_cell_row_is_not_a_unit_and_is_not_malformed(
        self, tmp_path: Path
    ) -> None:
        # The same event carries both row shapes. Reading the cell row as a
        # broken line put six false alarms in a clean recording's repair.
        log = _log(
            tmp_path,
            _cost_line(_LEAF_A, tokens=50),
            _journalled("d1-gated-r0", _LEAF_A),
            _cell_journalled("d1-gated-r0"),
        )

        with structlog.testing.capture_logs() as logs:
            attributed = tokens_by_unit(log)

        assert attributed == {("d1-gated-r0", _LEAF_A): 50}
        assert not [
            entry
            for entry in logs
            if entry["event"] == "evals.recursion_depth.spend_log_malformed"
        ]

    def test_a_line_the_parser_cannot_read_is_reported(self, tmp_path: Path) -> None:
        # A call the repair cannot even size is missing from both the attributed
        # total and the unclaimed one, so nothing else would show it.
        log = _log(
            tmp_path,
            "cost.recorded \x1b[36mcall_category\x1b[0m=\x1b[35mproductive\x1b[0m "
            "\x1b[36minput_tokens\x1b[0m=\x1b[35m9",
        )

        with structlog.testing.capture_logs() as logs:
            tokens_by_unit(log)

        malformed = [
            entry
            for entry in logs
            if entry["event"] == "evals.recursion_depth.spend_log_malformed"
        ]
        assert malformed[0]["malformed_lines"] == 1


class TestApplyingIt:
    """What gets overwritten, and what must not be."""

    def test_a_zeroed_leaf_is_restored(self) -> None:
        cell = _cell(_unit(_LEAF_A, kind=LEAF, tokens=0))

        repaired = repair_cell_spend([cell], {("d1-gated-r0", _LEAF_A): 12_345})

        assert repaired[0].units[0].tokens == 12_345

    def test_a_unit_the_log_cannot_see_keeps_its_figure(self) -> None:
        cell = _cell(_unit("d1-gated-r0-plan", kind=PLAN, tokens=24_690))

        repaired = repair_cell_spend([cell], {})

        assert repaired[0].units[0].tokens == 24_690

    def test_a_cell_is_repaired_from_a_log_end_to_end(self, tmp_path: Path) -> None:
        # The two halves tested together, because passing repair_cell_spend a
        # hand-built mapping is what let a tokens_by_unit that zeroed every plan
        # unit ship green.
        log = _log(
            tmp_path,
            _cost_line(_SHARED_ROOT, tokens=300),
            _journalled("d1-gated-r0", "d1-gated-r0-plan"),
            _cost_line(_LEAF_A, tokens=500),
            _journalled("d1-gated-r0", _LEAF_A),
            _cost_line(_SHARED_ROOT, tokens=40),
            _journalled("d1-gated-r0", _SHARED_ROOT),
        )
        cell = _cell(
            _unit("d1-gated-r0-plan", kind=PLAN, tokens=300),
            _unit(_LEAF_A, kind=LEAF, tokens=0),
            _unit(_SHARED_ROOT, kind=MERGE, tokens=40),
        )

        repaired = repair_cell_spend([cell], tokens_by_unit(log))

        assert [unit.tokens for unit in repaired[0].units] == [300, 500, 40]
