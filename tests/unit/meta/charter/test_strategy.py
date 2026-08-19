"""Unit tests for the LLM-backed charter interview strategy."""

from datetime import UTC, datetime

import pytest

from synthorg.communication.conversation.enums import ConversationRole
from synthorg.core.types import NotBlankStr
from synthorg.meta.charter.config import CharterConfig
from synthorg.meta.charter.strategy import LLMCharterInterviewer
from synthorg.meta.chief_of_staff.models import ConversationTurn
from synthorg.meta.errors import CharterInterviewResponseInvalidError
from tests._shared import as_uuid, sid
from tests._shared.model_binding import bound_ref, one_connection
from tests._shared.scripted_provider import ScriptedProvider, make_text_response

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)

_QUESTION_JSON = (
    '{"needs_more": true, "next_question": "What is the budget?", "draft": null}'
)
_DRAFT_JSON = (
    '{"needs_more": false, "next_question": null, "draft": {'
    '"title": "Memory layer", "brief": "Build a better memory layer.", '
    '"goals": ["beat baseline"], "constraints": ["self-hostable"], '
    '"success_criteria": ["recall +10%"], '
    '"scope": {"in_scope": ["retrieval"], "out_of_scope": ["billing"]}, '
    '"envelope": {"amount": 5000, "currency": "USD", '
    '"deadline": null, "time_horizon": "1 month"}, '
    '"project_id": null, "proposed_project_name": "memory-layer", '
    '"proposed_project_description": "A better memory layer.", '
    '"assumed_facets": []}}'
)


def _history() -> tuple[ConversationTurn, ...]:
    return (
        ConversationTurn(
            id=as_uuid("t-0"),
            conversation_id=sid("conv-1"),
            sequence=0,
            role=ConversationRole.USER,
            content=NotBlankStr("build a better alternative to the memory tool"),
            created_at=_NOW,
        ),
    )


def _interviewer(provider: ScriptedProvider) -> LLMCharterInterviewer:
    return LLMCharterInterviewer(connections=one_connection(provider))


class TestLLMCharterInterviewer:
    async def test_parses_question_branch(self) -> None:
        provider = ScriptedProvider(response=make_text_response(_QUESTION_JSON))
        decision = await _interviewer(provider).run_turn(
            _history(),
            project_id=None,
            config=CharterConfig(interview_model=bound_ref()),
        )
        assert decision.needs_more is True
        assert decision.next_question == "What is the budget?"
        assert decision.draft is None

    async def test_parses_draft_branch(self) -> None:
        provider = ScriptedProvider(response=make_text_response(_DRAFT_JSON))
        decision = await _interviewer(provider).run_turn(
            _history(),
            project_id=None,
            config=CharterConfig(interview_model=bound_ref()),
        )
        assert decision.needs_more is False
        assert decision.draft is not None
        assert decision.draft.proposed_project_name == "memory-layer"
        assert decision.draft.envelope.amount == pytest.approx(5000.0)

    async def test_malformed_json_raises(self) -> None:
        provider = ScriptedProvider(response=make_text_response("not json at all"))
        with pytest.raises(CharterInterviewResponseInvalidError):
            await _interviewer(provider).run_turn(
                _history(),
                project_id=None,
                config=CharterConfig(interview_model=bound_ref()),
            )

    async def test_a_draft_omitting_its_assumptions_is_retried(self) -> None:
        # The coverage press fires on a non-empty value, so a draft that left
        # the key out would read as "assumed nothing", skip the press, and put
        # the org's own proposals to the operator as their own answers. The
        # planner is asked again instead.
        without = _DRAFT_JSON.replace(', "assumed_facets": []', "")
        provider = ScriptedProvider(response=make_text_response(without))
        with pytest.raises(CharterInterviewResponseInvalidError):
            await _interviewer(provider).run_turn(
                _history(),
                project_id=None,
                config=CharterConfig(interview_model=bound_ref()),
            )

    async def test_schema_violation_raises(self) -> None:
        # needs_more true but no next_question violates the XOR contract.
        bad = '{"needs_more": true, "next_question": null, "draft": null}'
        provider = ScriptedProvider(response=make_text_response(bad))
        with pytest.raises(CharterInterviewResponseInvalidError):
            await _interviewer(provider).run_turn(
                _history(),
                project_id=None,
                config=CharterConfig(interview_model=bound_ref()),
            )


class TestOneMalformedAnswerDoesNotEndTheInterview:
    """This is the only door into the product.

    A live interview died on turn three because the model returned the
    charter's budget object where the decision envelope goes, and the
    operator was shown an exception class name. One badly-shaped reply
    must not close the one intake path the product has.
    """

    #: The exact shape that killed the live run: the envelope's fields at
    #: the top level, so ``needs_more`` is missing and three keys are extra.
    _FLATTENED = (
        '{"amount": 100, "currency": "USD", "deadline": null, '
        '"time_horizon": "1 week from approval"}'
    )

    async def test_the_model_is_asked_again_and_the_turn_succeeds(self) -> None:
        provider = ScriptedProvider(
            responses=[
                make_text_response(self._FLATTENED),
                make_text_response(_QUESTION_JSON),
            ]
        )

        decision = await _interviewer(provider).run_turn(
            _history(),
            project_id=None,
            config=CharterConfig(interview_model=bound_ref()),
        )

        assert decision.next_question == "What is the budget?"
        assert len(provider.complete_calls) == 2

    async def test_the_repair_turn_carries_the_refusal_and_the_output(self) -> None:
        """The model needs both to correct itself, not a bare "try again"."""
        provider = ScriptedProvider(
            responses=[
                make_text_response(self._FLATTENED),
                make_text_response(_QUESTION_JSON),
            ]
        )

        await _interviewer(provider).run_turn(
            _history(),
            project_id=None,
            config=CharterConfig(interview_model=bound_ref()),
        )

        repair = provider.complete_calls[1][0][-1].content or ""
        assert "needs_more" in repair
        assert '"amount": 100' in repair

    async def test_a_second_malformed_answer_gives_up(self) -> None:
        """A model that cannot hold the schema twice will not on a third try."""
        provider = ScriptedProvider(
            responses=[
                make_text_response(self._FLATTENED),
                make_text_response(self._FLATTENED),
            ]
        )

        with pytest.raises(CharterInterviewResponseInvalidError) as exc_info:
            await _interviewer(provider).run_turn(
                _history(),
                project_id=None,
                config=CharterConfig(interview_model=bound_ref()),
            )

        # What the operator reads in chat: the setting to change, and that
        # nothing was lost. A class name told them neither.
        assert "charter.interview_model" in str(exc_info.value)
        assert len(provider.complete_calls) == 2

    async def test_uses_configured_model(self) -> None:
        provider = ScriptedProvider(response=make_text_response(_QUESTION_JSON))
        config = CharterConfig(
            interview_model=NotBlankStr(bound_ref("example-capable-001"))
        )
        await _interviewer(provider).run_turn(
            _history(), project_id=None, config=config
        )
        assert provider.complete_calls[0][1] == "example-capable-001"
