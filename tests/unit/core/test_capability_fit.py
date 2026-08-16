"""Tests for the capability-fit ladder."""

import pytest

from synthorg.core.capability_fit import (
    bands_by_fit,
    best_by_fit,
    partition_by_fit,
)

pytestmark = pytest.mark.unit


def _rank(candidate: tuple[str, int]) -> int:
    return candidate[1]


BASIC = ("basic-one", 0)
BASIC_TWO = ("basic-two", 0)
CAPABLE = ("capable-one", 1)
EXPERT = ("expert-one", 2)
EXPERT_TWO = ("expert-two", 2)


class TestPartitionByFit:
    def test_an_exact_rung_wins_over_a_stronger_one(self) -> None:
        assert partition_by_fit([CAPABLE, EXPERT], _rank, 1) == ((CAPABLE,), "match")

    def test_the_nearest_rung_above_when_no_exact_match(self) -> None:
        assert partition_by_fit([BASIC, EXPERT], _rank, 1) == ((EXPERT,), "higher")

    def test_the_nearest_rung_below_when_nothing_reaches(self) -> None:
        assert partition_by_fit([BASIC, BASIC_TWO], _rank, 2) == (
            (BASIC, BASIC_TWO),
            "lower",
        )

    def test_an_empty_pool_has_no_band(self) -> None:
        assert partition_by_fit([], _rank, 1) is None


class TestBandsByFit:
    def test_every_rung_is_offered_in_preference_order(self) -> None:
        """The ladder is a preference, so a caller can keep walking it.

        A band that contains nobody the caller can use is not the same as a
        pool with nobody in it. Returning only the first band makes an
        over-qualified specialist unreachable whenever any exact-rung stranger
        exists, which strands work against a roster that staffs every role it
        names.
        """
        pool = [BASIC, CAPABLE, EXPERT, EXPERT_TWO]
        assert list(bands_by_fit(pool, _rank, 1)) == [
            ((CAPABLE,), "match"),
            ((EXPERT, EXPERT_TWO), "higher"),
            ((BASIC,), "lower"),
        ]

    def test_higher_rungs_ascend_one_at_a_time(self) -> None:
        # A candidate two rungs over is a worse fit AND more expensive, so it
        # is offered only after everything between it and the requirement.
        pool = [CAPABLE, EXPERT]
        assert list(bands_by_fit(pool, _rank, 0)) == [
            ((CAPABLE,), "higher"),
            ((EXPERT,), "higher"),
        ]

    def test_lower_rungs_descend_one_at_a_time(self) -> None:
        pool = [BASIC, CAPABLE]
        assert list(bands_by_fit(pool, _rank, 2)) == [
            ((CAPABLE,), "lower"),
            ((BASIC,), "lower"),
        ]

    def test_an_empty_pool_offers_nothing(self) -> None:
        assert list(bands_by_fit([], _rank, 1)) == []

    def test_the_first_band_is_what_partition_by_fit_returns(self) -> None:
        """The single-answer helper stays the head of the ladder.

        Stated as a test because the two would otherwise be free to disagree,
        and every existing caller reads the head.
        """
        for required in (0, 1, 2):
            pool = [BASIC, CAPABLE, EXPERT]
            head = next(iter(bands_by_fit(pool, _rank, required)), None)
            assert head == partition_by_fit(pool, _rank, required)


class TestBestByFit:
    def test_ties_break_deterministically_within_the_winning_band(self) -> None:
        chosen = best_by_fit([EXPERT_TWO, EXPERT], _rank, 2, lambda c: c[0])
        assert chosen == (EXPERT, "match")

    def test_an_empty_pool_has_no_best(self) -> None:
        assert best_by_fit([], _rank, 1, lambda c: c[0]) is None
