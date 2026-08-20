"""An em-dash cannot cross the charter interview.

Both halves of an interview turn are agent output the organisation keeps or
sends: the question goes to the operator in chat, and the draft becomes the
persisted charter whose ``proposed_project_name`` names the project the run is
delivered under. A live run shipped both, naming a project "Falling Blocks
[em-dash] Browser Puzzle Game" and asking two of its five questions with the one
character the policy ships a hard rule against.

The em-dash is built at runtime (``chr(0x2014)``) so no literal U+2014 lands in
committed test source.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from synthorg.communication.conversation.enums import ConversationRole
from synthorg.core.types import NotBlankStr
from synthorg.engine.output_style.models import OutputStyleConfig
from synthorg.engine.output_style.service import (
    OutputStylePolicyService,
    current_output_policy_service,
    set_output_policy_service,
)
from synthorg.meta.charter.config import CharterConfig
from synthorg.meta.charter.models import InterviewDecision
from synthorg.meta.charter.strategy import LLMCharterInterviewer
from synthorg.meta.chief_of_staff.models import ConversationTurn
from synthorg.meta.errors import CharterInterviewResponseInvalidError
from tests._shared import as_uuid, sid
from tests._shared.model_binding import bound_ref, one_connection
from tests._shared.scripted_provider import ScriptedProvider, make_text_response

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
_EM_DASH = chr(0x2014)

_CLEAN_QUESTION = (
    '{"needs_more": true, "next_question": "What is the budget?", "draft": null}'
)
_DIRTY_QUESTION = (
    '{"needs_more": true, "next_question": "What does done look like '
    f'{_EM_DASH} in measurable terms?", "draft": null}}'
)


def _draft_json(project_name: str) -> str:
    return (
        '{"needs_more": false, "next_question": null, "draft": {'
        '"title": "Memory layer", "brief": "Build a better memory layer.", '
        '"goals": ["beat baseline"], "constraints": ["self-hostable"], '
        '"success_criteria": ["recall +10%"], '
        '"scope": {"in_scope": ["retrieval"], "out_of_scope": ["billing"]}, '
        '"envelope": {"amount": 5000, "currency": "USD", '
        '"deadline": null, "time_horizon": "1 month"}, '
        f'"project_id": null, "proposed_project_name": "{project_name}", '
        '"proposed_project_description": "A better memory layer.", '
        '"assumed_facets": []}}'
    )


@pytest.fixture
def _wired_service() -> Iterator[None]:
    # Restores whatever was bound before rather than forcing None: on a shared
    # xdist worker that may be a real service another test is relying on.
    previous = current_output_policy_service()
    set_output_policy_service(OutputStylePolicyService.from_config(OutputStyleConfig()))
    try:
        yield
    finally:
        set_output_policy_service(previous)


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


async def _run(provider: ScriptedProvider) -> InterviewDecision:
    return await LLMCharterInterviewer(connections=one_connection(provider)).run_turn(
        _history(),
        project_id=None,
        config=CharterConfig(interview_model=bound_ref()),
    )


@pytest.mark.usefixtures("_wired_service")
class TestCharterInterviewBoundary:
    async def test_a_question_carrying_one_is_asked_again(self) -> None:
        provider = ScriptedProvider(
            responses=[
                make_text_response(_DIRTY_QUESTION),
                make_text_response(_CLEAN_QUESTION),
            ]
        )

        decision = await _run(provider)

        assert decision.next_question == "What is the budget?"
        assert len(provider.complete_calls) == 2

    async def test_the_repair_turn_says_it_was_the_wording(self) -> None:
        # A schema refusal asks for the same content in a different shape. This
        # one has to ask for different content, or the model sends the same
        # characters back and the interview spends its one repair for nothing.
        provider = ScriptedProvider(
            responses=[
                make_text_response(_DIRTY_QUESTION),
                make_text_response(_CLEAN_QUESTION),
            ]
        )

        await _run(provider)

        repair = provider.complete_calls[1][0][-1].content or ""
        assert "house style" in repair
        assert "Reword it" in repair

    async def test_a_project_name_carrying_one_is_refused(self) -> None:
        # The name the whole delivered project is handed over under.
        provider = ScriptedProvider(
            responses=[
                make_text_response(_draft_json(f"Falling Blocks {_EM_DASH} Puzzle")),
                make_text_response(_draft_json("Falling Blocks Puzzle")),
            ]
        )

        decision = await _run(provider)

        assert decision.draft is not None
        assert decision.draft.proposed_project_name == "Falling Blocks Puzzle"

    async def test_a_second_dirty_answer_gives_up_rather_than_shipping_it(
        self,
    ) -> None:
        provider = ScriptedProvider(
            responses=[
                make_text_response(_DIRTY_QUESTION),
                make_text_response(_DIRTY_QUESTION),
            ]
        )

        with pytest.raises(CharterInterviewResponseInvalidError):
            await _run(provider)

    async def test_clean_prose_passes_through_untouched(self) -> None:
        provider = ScriptedProvider(response=make_text_response(_CLEAN_QUESTION))

        decision = await _run(provider)

        assert decision.next_question == "What is the budget?"
        assert len(provider.complete_calls) == 1
