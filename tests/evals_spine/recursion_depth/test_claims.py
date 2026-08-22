# module-kind: tests
"""A planner's criterion text and a requirement id are one fact, both ways."""

import pytest

from evals.recursion_depth.claims import (
    RequirementId,
    criterion_for,
    requirement_ids_of,
)

pytestmark = pytest.mark.unit

_KNOWN = (RequirementId("R01"), RequirementId("R02"), RequirementId("R42"))


class TestRoundTrip:
    def test_a_minted_criterion_resolves_back_to_its_own_id(self) -> None:
        """The mint and the parse are the pair that silently drifted apart."""
        minted = [criterion_for(one) for one in _KNOWN]

        assert requirement_ids_of(minted, known=_KNOWN, unit="u").ids == _KNOWN

    def test_a_reworded_criterion_still_resolves_on_its_id(self) -> None:
        resolved = requirement_ids_of(
            ("Requirement R02 holds for every input row",), known=_KNOWN, unit="u"
        )

        assert resolved.ids == ("R02",)


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
            (criterion_for(RequirementId("R99")), criterion_for(RequirementId("R01"))),
            known=_KNOWN,
            unit="u",
        )

        assert resolved.ids == ("R01",)

    def test_prose_naming_no_requirement_resolves_to_nothing(self) -> None:
        assert requirement_ids_of(("ship it",), known=_KNOWN, unit="u").ids == ()


class TestShape:
    def test_one_requirement_claimed_twice_is_counted_once(self) -> None:
        """Survival is a set question; a repeated claim is not more work."""
        resolved = requirement_ids_of(
            (criterion_for(RequirementId("R01")), "R01 also covered here"),
            known=_KNOWN,
            unit="u",
        )

        assert resolved.ids == ("R01",)

    def test_a_claim_naming_two_requirements_yields_both(self) -> None:
        resolved = requirement_ids_of(
            ("R01 and R02 are satisfied together",), known=_KNOWN, unit="u"
        )

        assert resolved.ids == ("R01", "R02")

    def test_a_longer_id_is_not_matched_by_a_shorter_prefix(self) -> None:
        """Word boundaries, or R4 would claim R42's requirement."""
        resolved = requirement_ids_of(("R4 is satisfied",), known=_KNOWN, unit="u")

        assert resolved.ids == ()


class TestTheUnresolvedCountIsCountedNotDerived:
    """The two numbers are in different units, so no subtraction relates them.

    Derived as ``len(claims) - len(ids)`` the count is wrong in both
    directions, and an ordinary planner reaches both: the record refuses a
    negative at ``ge=0``, which discards a cell whose every leaf was already
    paid for, and an over-count inflates the one drift signal the report's
    caveat exists to carry.
    """

    def test_a_claim_naming_two_requirements_leaves_none_unresolved(self) -> None:
        """One claim, two ids: the derived form would answer -1 and raise."""
        resolved = requirement_ids_of(
            ("R01 and R02 are satisfied together",), known=_KNOWN, unit="u"
        )

        assert len(resolved.ids) > 1
        assert resolved.unresolved == 0

    def test_two_claims_naming_one_requirement_leave_none_unresolved(self) -> None:
        """Two claims, one id: the derived form would report a drift of 1."""
        resolved = requirement_ids_of(
            (criterion_for(RequirementId("R01")), "R01 also covered here"),
            known=_KNOWN,
            unit="u",
        )

        assert resolved.ids == ("R01",)
        assert resolved.unresolved == 0

    def test_each_claim_naming_nothing_counts_once(self) -> None:
        resolved = requirement_ids_of(
            ("ship it", "make it fast", criterion_for(RequirementId("R01"))),
            known=_KNOWN,
            unit="u",
        )

        assert resolved.ids == ("R01",)
        assert resolved.unresolved == 2

    def test_an_invented_requirement_counts_as_unresolved(self) -> None:
        """R99 parses as an id and still names nothing this spec defines."""
        resolved = requirement_ids_of(
            (criterion_for(RequirementId("R99")),), known=_KNOWN, unit="u"
        )

        assert resolved.ids == ()
        assert resolved.unresolved == 1
