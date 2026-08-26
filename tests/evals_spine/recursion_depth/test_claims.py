# module-kind: tests
"""A planner's criterion text and a requirement id are one fact, both ways."""

import pytest

from evals.errors import RecursionDepthClaimUnresolvableError
from evals.recursion_depth.claims import (
    RequirementId,
    criterion_for,
    requirement_ids_of,
)

pytestmark = pytest.mark.unit

_KNOWN = (RequirementId("R01"), RequirementId("R02"), RequirementId("R42"))

_TITLES = {
    RequirementId("R01"): "The header row names the columns",
    RequirementId("R02"): "An integer column compares and sorts numerically",
    RequirementId("R42"): "A dash reads the statement from stdin",
}


class TestRoundTrip:
    def test_a_minted_criterion_resolves_back_to_its_own_id(self) -> None:
        """The mint and the parse are the pair that silently drifted apart."""
        minted = [criterion_for(one, _TITLES[one]) for one in _KNOWN]

        assert requirement_ids_of(minted, known=_KNOWN, unit="u") == _KNOWN

    def test_the_criterion_carries_the_requirement_prose(self) -> None:
        """A planner at depth is shown this and no specification beside it.

        The child task it is decomposing describes the unit, not the spec, so
        an id on its own names something the planner cannot allocate against.
        """
        minted = criterion_for(RequirementId("R01"), _TITLES[RequirementId("R01")])

        assert "R01" in minted
        assert "The header row names the columns" in minted

    def test_a_reworded_criterion_still_resolves_on_its_id(self) -> None:
        resolved = requirement_ids_of(
            ("Requirement R02 holds for every input row",), known=_KNOWN, unit="u"
        )

        assert resolved == ("R02",)


class TestUnresolvable:
    def test_an_invented_requirement_raises(self) -> None:
        """Dropped, it reads as an ordinary zero at every consumer.

        The brief renders it as "no such requirement" and the survival
        intersection matches it against nothing, neither of which looks like
        a fault, which is how 143 of them went unnoticed for a whole sweep.
        """
        with pytest.raises(RecursionDepthClaimUnresolvableError):
            requirement_ids_of(
                (criterion_for(RequirementId("R99"), "invented"),),
                known=_KNOWN,
                unit="u",
            )

    def test_prose_naming_no_requirement_raises(self) -> None:
        with pytest.raises(RecursionDepthClaimUnresolvableError):
            requirement_ids_of(("ship it",), known=_KNOWN, unit="u")

    def test_the_message_names_the_unit_and_the_claim(self) -> None:
        """A sweep of hundreds of units reports this once and stops."""
        with pytest.raises(RecursionDepthClaimUnresolvableError) as caught:
            requirement_ids_of(("ship it",), known=_KNOWN, unit="Build the lexer")

        detail = str(caught.value)
        assert "Build the lexer" in detail
        assert "ship it" in detail

    def test_a_resolvable_claim_beside_it_does_not_rescue_the_unit(self) -> None:
        with pytest.raises(RecursionDepthClaimUnresolvableError):
            requirement_ids_of(
                (
                    criterion_for(RequirementId("R01"), _TITLES[RequirementId("R01")]),
                    "ship it",
                ),
                known=_KNOWN,
                unit="u",
            )

    def test_claiming_nothing_resolves_to_nothing(self) -> None:
        """A pure-support unit advances no requirement, which is not a fault."""
        assert requirement_ids_of((), known=_KNOWN, unit="u") == ()


class TestShape:
    def test_one_requirement_claimed_twice_is_counted_once(self) -> None:
        """Survival is a set question; a repeated claim is not more work."""
        resolved = requirement_ids_of(
            (
                criterion_for(RequirementId("R01"), _TITLES[RequirementId("R01")]),
                "R01 also covered here",
            ),
            known=_KNOWN,
            unit="u",
        )

        assert resolved == ("R01",)

    def test_one_claim_naming_the_same_requirement_twice_yields_it_once(
        self,
    ) -> None:
        """The case that separates deduplication from append ordering.

        Dropping a repeat ACROSS claims works however the appends are written;
        dropping one WITHIN a single claim only works while each append lands
        before the next membership test.
        """
        resolved = requirement_ids_of(
            ("R01 and R01 are both satisfied",), known=_KNOWN, unit="u"
        )

        assert resolved == ("R01",)

    def test_a_repeat_within_a_claim_does_not_displace_a_later_requirement(
        self,
    ) -> None:
        resolved = requirement_ids_of(
            ("R02, R01, R02 all hold",), known=_KNOWN, unit="u"
        )

        assert resolved == ("R02", "R01")

    def test_a_claim_naming_two_requirements_yields_both(self) -> None:
        resolved = requirement_ids_of(
            ("R01 and R02 are satisfied together",), known=_KNOWN, unit="u"
        )

        assert resolved == ("R01", "R02")

    def test_a_longer_id_is_not_matched_by_a_shorter_prefix(self) -> None:
        """Word boundaries, or R4 would claim R42's requirement."""
        with pytest.raises(RecursionDepthClaimUnresolvableError):
            requirement_ids_of(("R4 is satisfied",), known=_KNOWN, unit="u")
