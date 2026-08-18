"""A charter never quietly asserts a facet the human was never asked about.

The interview decided for itself when it had heard enough, and a live run
showed the cost: one question, then a charter whose goals, success criteria,
scope, envelope and project were all supplied by the model and rendered
beside the single elicited answer with nothing to tell them apart. The
operator approves a scope they never agreed, and ``charter_id`` then
authorises an initiative against it.

The invariant pinned here is that the model's declaration of what it is
assuming is an INPUT, and the decision it feeds belongs to the service: a
draft that fills a facet the human did not settle is put back to them once,
and whatever survives that is recorded on the charter rather than rendered as
though they had said it.
"""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.charter._facet_coverage import (
    COVERAGE_PRESS_LEAD_IN,
    coverage_question,
)
from synthorg.meta.charter.enums import CharterFacet
from synthorg.meta.charter.models import InterviewDecision, InterviewTurnArgs
from tests.unit.meta.charter.fakes import draft as _draft
from tests.unit.meta.charter.fakes import service as _service

pytestmark = pytest.mark.unit

_ASSUMES_CRITERIA = InterviewDecision(
    needs_more=False,
    draft=_draft(assumed_facets=(CharterFacet.SUCCESS_CRITERIA,)),
)


class TestADraftIsPutBackBeforeItStands:
    async def test_an_assumed_facet_becomes_a_question(self) -> None:
        service, charters = _service([_ASSUMES_CRITERIA])

        result = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("a vague idea"), created_by="u1")
        )

        assert result.status == "needs_more"
        assert result.next_question is not None
        assert result.next_question.startswith(COVERAGE_PRESS_LEAD_IN)
        # Nothing is persisted: the operator has not been shown a charter
        # they might approve without seeing what is in it.
        assert charters.items == {}

    async def test_the_question_names_the_facet_in_the_human_s_terms(self) -> None:
        service, _ = _service([_ASSUMES_CRITERIA])

        result = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("a vague idea"), created_by="u1")
        )

        assert result.next_question is not None
        assert "how you want to be able to tell it is done" in result.next_question

    async def test_a_draft_that_assumes_nothing_stands_at_once(self) -> None:
        service, charters = _service(
            [InterviewDecision(needs_more=False, draft=_draft())]
        )

        result = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("a clear idea"), created_by="u1")
        )

        assert result.status == "drafted"
        assert len(charters.items) == 1


class TestThePressHappensOnceAndIsThenRecorded:
    async def test_a_second_draft_stands_even_if_it_still_assumes(self) -> None:
        # The human may have nothing to add, or may want the org to decide.
        # Asking again would be a loop with no exit.
        service, charters = _service([_ASSUMES_CRITERIA, _ASSUMES_CRITERIA])
        first = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("a vague idea"), created_by="u1")
        )

        second = await service.run_turn(
            InterviewTurnArgs(
                message=NotBlankStr("you decide"),
                created_by="u1",
                conversation_id=NotBlankStr(first.conversation_id),
            )
        )

        assert second.status == "drafted"
        assert len(charters.items) == 1

    async def test_what_survived_the_press_is_recorded_on_the_charter(self) -> None:
        service, _ = _service([_ASSUMES_CRITERIA, _ASSUMES_CRITERIA])
        first = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("a vague idea"), created_by="u1")
        )

        second = await service.run_turn(
            InterviewTurnArgs(
                message=NotBlankStr("you decide"),
                created_by="u1",
                conversation_id=NotBlankStr(first.conversation_id),
            )
        )

        assert second.charter is not None
        assert second.charter.assumed_facets == (CharterFacet.SUCCESS_CRITERIA,)

    async def test_a_settled_facet_leaves_nothing_recorded(self) -> None:
        service, _ = _service(
            [_ASSUMES_CRITERIA, InterviewDecision(needs_more=False, draft=_draft())]
        )
        first = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("a vague idea"), created_by="u1")
        )

        second = await service.run_turn(
            InterviewTurnArgs(
                message=NotBlankStr("done means the test suite passes"),
                created_by="u1",
                conversation_id=NotBlankStr(first.conversation_id),
            )
        )

        assert second.charter is not None
        assert second.charter.assumed_facets == ()


class TestTheCoverageQuestion:
    def test_every_facet_has_something_to_ask(self) -> None:
        # A facet the enum declares but the question cannot phrase would
        # raise at the moment the press fires, which is a live interview.
        asked = coverage_question(tuple(CharterFacet))
        assert asked.startswith(COVERAGE_PRESS_LEAD_IN)
        assert asked.count("\n- ") == len(CharterFacet)

    def test_a_facet_named_twice_is_asked_once(self) -> None:
        asked = coverage_question(
            (CharterFacet.SCOPE, CharterFacet.SCOPE, CharterFacet.GOALS)
        )
        assert asked.count("\n- ") == 2

    def test_the_order_does_not_depend_on_the_declaration_order(self) -> None:
        # Two drafts naming the same facets have to ask the same question,
        # or the press reads as a different question each time.
        forwards = coverage_question((CharterFacet.GOALS, CharterFacet.ENVELOPE))
        backwards = coverage_question((CharterFacet.ENVELOPE, CharterFacet.GOALS))
        assert forwards == backwards
