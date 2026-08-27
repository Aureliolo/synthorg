"""A decomposition must be able to say where it has got to.

A recursive decomposition persists its tree once, at the end, so the plan it is
writing reads ``PLANNING`` with zero items for the whole run. That is correct
and it left the operator with nothing: a live run sat at zero for 54 minutes
under a page promising "items appear as they are written", and the only way to
tell a working decomposition from a hung one was the backend log.

These pin the two halves. The ledger already held every number the question
needs, so the snapshot is asserted against it directly; and the reporting is
best-effort by contract, because a decomposition is minutes to hours of real
provider spend and losing the progress line must never cost the tree.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.decomposition_progress import DecompositionProgress
from synthorg.engine.decomposition._recursion import TreeSessionLedger

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


class TestTheLedgerCanDescribeItself:
    def test_a_fresh_tree_has_spent_nothing(self) -> None:
        ledger = TreeSessionLedger(remaining=40, limit=40)

        snapshot = ledger.progress(now=_NOW)

        assert snapshot.sessions_spent == 0
        assert snapshot.sessions_limit == 40
        assert snapshot.deepest_level == 0
        assert snapshot.units_planned == 0

    def test_spend_is_derived_from_the_budget_rather_than_counted_twice(self) -> None:
        # Two counters over one budget is the second answer this class exists
        # to avoid, so the snapshot subtracts rather than tracking its own.
        ledger = TreeSessionLedger(remaining=40, limit=40)
        for _ in range(3):
            ledger.take()

        assert ledger.progress(now=_NOW).sessions_spent == 3

    def test_a_ledger_with_no_limit_never_reports_negative_spend(self) -> None:
        # A harness builds one without a limit, and ``sessions_spent`` is
        # ``ge=0``: an unfloored subtraction would refuse the model outright.
        ledger = TreeSessionLedger(remaining=5)
        ledger.take()

        assert ledger.progress(now=_NOW).sessions_spent == 0

    def test_levels_accumulate_across_the_walk(self) -> None:
        ledger = TreeSessionLedger(remaining=40, limit=40)
        ledger.record_level(depth=0, units=4)
        ledger.record_level(depth=1, units=3)
        ledger.record_level(depth=1, units=2)

        snapshot = ledger.progress(now=_NOW)

        assert snapshot.deepest_level == 1
        assert snapshot.units_planned == 9

    def test_the_deepest_level_never_goes_backwards(self) -> None:
        # The walk returns to shallower levels after recursing, and a depth
        # read as the CURRENT one would flip between 3 and 1 as the tree
        # unwound, which reads as progress being lost.
        ledger = TreeSessionLedger(remaining=40, limit=40)
        ledger.record_level(depth=3, units=1)
        ledger.record_level(depth=1, units=1)

        assert ledger.progress(now=_NOW).deepest_level == 3

    def test_the_snapshot_carries_the_time_it_was_taken(self) -> None:
        # The one field that separates a working decomposition from a stalled
        # one: every other number is unchanged while a session runs.
        ledger = TreeSessionLedger(remaining=40, limit=40)

        assert ledger.progress(now=_NOW).updated_at == _NOW


class TestTheProgressModelRefusesNonsense:
    def test_a_negative_count_is_refused(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal to 0"):
            DecompositionProgress(
                sessions_spent=-1,
                sessions_limit=40,
                deepest_level=0,
                units_planned=0,
                updated_at=_NOW,
            )

    def test_a_naive_timestamp_is_refused(self) -> None:
        # Every other timestamp in the product is tz-aware UTC, and a naive one
        # here would render as a different time to every operator.
        with pytest.raises(ValueError, match=r"[Tt]imezone"):
            DecompositionProgress(
                sessions_spent=0,
                sessions_limit=40,
                deepest_level=0,
                units_planned=0,
                updated_at=datetime(2026, 8, 26, 12, 0),  # noqa: DTZ001 -- the point
            )
