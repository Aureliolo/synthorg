# module-kind: tests
"""A planner's criterion text and a requirement id are one fact, both ways."""

import pytest

from evals.recursion_depth.claims import criterion_for, requirement_ids_of

pytestmark = pytest.mark.unit

_KNOWN = ("R01", "R02", "R42")


class TestRoundTrip:
    def test_a_minted_criterion_resolves_back_to_its_own_id(self) -> None:
        """The mint and the parse are the pair that silently drifted apart."""
        minted = [criterion_for(one) for one in _KNOWN]

        assert requirement_ids_of(minted, known=_KNOWN, unit="u") == _KNOWN

    def test_a_reworded_criterion_still_resolves_on_its_id(self) -> None:
        resolved = requirement_ids_of(
            ("Requirement R02 holds for every input row",), known=_KNOWN, unit="u"
        )

        assert resolved == ("R02",)


class TestUnresolvable:
    def test_an_invented_requirement_is_dropped_rather_than_passed_through(
        self,
    ) -> None:
        """Passed through it reads as an ordinary zero at every consumer.

        The brief renders it as "no such requirement" and the survival
        intersection matches it against nothing, neither of which looks like
        a fault.
        """
        resolved = requirement_ids_of(
            (criterion_for("R99"), criterion_for("R01")), known=_KNOWN, unit="u"
        )

        assert resolved == ("R01",)

    def test_prose_naming_no_requirement_resolves_to_nothing(self) -> None:
        assert requirement_ids_of(("ship it",), known=_KNOWN, unit="u") == ()


class TestShape:
    def test_one_requirement_claimed_twice_is_counted_once(self) -> None:
        """Survival is a set question; a repeated claim is not more work."""
        resolved = requirement_ids_of(
            (criterion_for("R01"), "R01 also covered here"), known=_KNOWN, unit="u"
        )

        assert resolved == ("R01",)

    def test_a_claim_naming_two_requirements_yields_both(self) -> None:
        resolved = requirement_ids_of(
            ("R01 and R02 are satisfied together",), known=_KNOWN, unit="u"
        )

        assert resolved == ("R01", "R02")

    def test_a_longer_id_is_not_matched_by_a_shorter_prefix(self) -> None:
        """Word boundaries, or R4 would claim R42's requirement."""
        assert requirement_ids_of(("R4 is satisfied",), known=_KNOWN, unit="u") == ()
