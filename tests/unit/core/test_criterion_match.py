# module-kind: tests
"""Deciding whether a claim names an objective criterion, once.

The property the whole recursion rests on is asserted first: a match answers
with the OBJECTIVE's text rather than the claim's. Answering with the claim's
drifts the vocabulary by one normalisation step per level, which is the defect
this module exists to close.
"""

import pytest

from synthorg.core.criterion_match import (
    criterion_key,
    describe_unnamed_claims,
    matched_criteria,
    unmatched_claims,
)
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit

_OBJECTIVE: tuple[NotBlankStr, ...] = (
    NotBlankStr("R01: The header row names the columns"),
    NotBlankStr("R02: An integer column compares and sorts numerically"),
    NotBlankStr("R03: A decimal column reads as a float"),
)


class _Unit:
    """The smallest thing that claims, for the message helper."""

    def __init__(self, title: str, satisfies: tuple[str, ...]) -> None:
        self.title = title
        self.satisfies = satisfies


class TestTheKey:
    def test_case_is_folded(self) -> None:
        assert criterion_key("R01: The Header") == criterion_key("r01: the header")

    def test_surrounding_whitespace_is_trimmed(self) -> None:
        assert criterion_key("  R01: The Header  ") == criterion_key("R01: The Header")

    def test_an_internal_whitespace_run_collapses(self) -> None:
        assert criterion_key("R01:  The\theader") == criterion_key("R01: The header")

    def test_a_different_sentence_is_a_different_key(self) -> None:
        assert criterion_key("R01: The header") != criterion_key("R01: the footer")


class TestAMatchAnswersWithTheObjective:
    def test_the_canonical_text_wins_over_the_claim(self) -> None:
        """The property that stops the vocabulary drifting per level.

        Answering with the claim's spelling would hand the level below a
        vocabulary one normalisation step away from the one above it, and
        four levels of that is a criterion nothing can match.
        """
        matched = matched_criteria(
            ("  r01:   THE HEADER row NAMES the columns ",), objective=_OBJECTIVE
        )

        assert matched == (NotBlankStr("R01: The header row names the columns"),)

    def test_objective_order_is_preserved(self) -> None:
        matched = matched_criteria(
            (str(_OBJECTIVE[2]), str(_OBJECTIVE[0])), objective=_OBJECTIVE
        )

        assert matched == (_OBJECTIVE[0], _OBJECTIVE[2])

    def test_two_claims_naming_one_criterion_yield_it_once(self) -> None:
        matched = matched_criteria(
            (str(_OBJECTIVE[1]), str(_OBJECTIVE[1]).upper()), objective=_OBJECTIVE
        )

        assert matched == (_OBJECTIVE[1],)

    def test_an_invented_claim_matches_nothing(self) -> None:
        assert matched_criteria(("Ship it",), objective=_OBJECTIVE) == ()

    def test_no_objective_matches_nothing(self) -> None:
        """An objective declaring no criteria has no coverage to claim."""
        assert matched_criteria((str(_OBJECTIVE[0]),), objective=()) == ()


class TestWhatNamedNothing:
    def test_an_invented_claim_is_returned_as_written(self) -> None:
        """Quoted back verbatim, so the next turn compares two lists."""
        assert unmatched_claims(
            ("Ship it", str(_OBJECTIVE[0])), objective=_OBJECTIVE
        ) == ("Ship it",)

    def test_claims_are_returned_in_the_order_written(self) -> None:
        assert unmatched_claims(("b", "a"), objective=_OBJECTIVE) == ("b", "a")

    def test_a_claim_differing_only_in_case_and_spacing_names_something(self) -> None:
        assert (
            unmatched_claims(
                ("  r01:   the header row names the columns",), objective=_OBJECTIVE
            )
            == ()
        )

    def test_an_empty_objective_leaves_every_claim_unmatched(self) -> None:
        """A level answerable for nothing is one where any claim names it."""
        assert unmatched_claims(("a", "b"), objective=()) == ("a", "b")


class TestTheRefusal:
    def test_a_clean_plan_produces_no_message(self) -> None:
        units = (_Unit("Ingest", (str(_OBJECTIVE[0]),)),)

        assert describe_unnamed_claims(units, objective=_OBJECTIVE) == ()

    def test_an_item_claiming_nothing_produces_no_message(self) -> None:
        """A genuine pure-support item claims nothing, which stays legal."""
        units = (_Unit("Choose an architecture", ()),)

        assert describe_unnamed_claims(units, objective=_OBJECTIVE) == ()

    def test_the_message_names_the_item_and_what_it_invented(self) -> None:
        units = (_Unit("Ingest", (str(_OBJECTIVE[0]), "Tests pass")),)

        message = describe_unnamed_claims(units, objective=_OBJECTIVE)[0]

        assert "Ingest" in message
        assert "Tests pass" in message

    def test_the_item_message_does_not_carry_the_criteria_list(self) -> None:
        """It rides the tail instead, once, however many items offend."""
        units = (_Unit("Ingest", ("Tests pass",)),)

        message = describe_unnamed_claims(units, objective=_OBJECTIVE)[0]

        assert str(_OBJECTIVE[0]) not in message

    def test_the_criteria_to_copy_from_are_quoted_in_full(self) -> None:
        """Every one of them: a partial list is one the planner cannot use."""
        units = (_Unit("Ingest", ("Tests pass",)),)

        tail = describe_unnamed_claims(units, objective=_OBJECTIVE)[-1]

        assert all(str(criterion) in tail for criterion in _OBJECTIVE)

    def test_the_criteria_are_stated_once_however_many_items_offend(self) -> None:
        """The list is one list. Repeating it per item multiplies it by the
        item count, and a plan may carry a thousand items.
        """
        units = tuple(_Unit(f"Item {index}", ("Ship it",)) for index in range(6))

        messages = describe_unnamed_claims(units, objective=_OBJECTIVE)

        quoting = [m for m in messages if str(_OBJECTIVE[0]) in m]
        assert len(quoting) == 1

    def test_one_message_per_offending_item(self) -> None:
        units = (
            _Unit("Ingest", ("Tests pass",)),
            _Unit("Lexer", (str(_OBJECTIVE[1]),)),
            _Unit("Parser", ("Ship it",)),
        )

        messages = describe_unnamed_claims(units, objective=_OBJECTIVE)

        assert len(messages) == 3
        assert "Ingest" in messages[0]
        assert "Parser" in messages[1]


class TestALevelAnswerableForNothing:
    """The case that left a whole subtree unchecked.

    A pure-support unit is judged oversized on its artifact count, with
    ``satisfies`` never entering the decision, so it is recursed into with an
    empty vocabulary. Skipping the check there let its descendants claim
    anything at all.
    """

    def test_a_claim_is_refused_when_the_level_states_no_criteria(self) -> None:
        units = (_Unit("Ingest", ("R01: something",)),)

        messages = describe_unnamed_claims(units, objective=())

        assert messages
        assert "Ingest" in messages[0]

    def test_the_refusal_says_there_is_nothing_to_claim(self) -> None:
        units = (_Unit("Ingest", ("R01: something",)),)

        tail = describe_unnamed_claims(units, objective=())[-1]

        assert "answerable for no objective criterion" in tail

    def test_an_item_claiming_nothing_is_still_accepted(self) -> None:
        """The subtree advances nothing and says so, which is honest."""
        units = (_Unit("Scaffold", ()), _Unit("Wire", ()))

        assert describe_unnamed_claims(units, objective=()) == ()
