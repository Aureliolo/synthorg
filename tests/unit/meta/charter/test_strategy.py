"""Unit tests for the LLM-backed charter interview strategy."""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import ConversationRole
from synthorg.core.types import NotBlankStr
from synthorg.meta.charter.config import CharterConfig
from synthorg.meta.charter.strategy import LLMCharterInterviewer
from synthorg.meta.chief_of_staff.models import ConversationTurn
from synthorg.meta.errors import CharterInterviewResponseInvalidError
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
    '"proposed_project_description": "A better memory layer."}}'
)


def _history() -> tuple[ConversationTurn, ...]:
    return (
        ConversationTurn(
            id="t-0",
            conversation_id="conv-1",
            sequence=0,
            role=ConversationRole.USER,
            content=NotBlankStr("build a better alternative to the memory tool"),
            created_at=_NOW,
        ),
    )


def _interviewer(provider: ScriptedProvider) -> LLMCharterInterviewer:
    return LLMCharterInterviewer(provider=provider, config=CharterConfig())


class TestLLMCharterInterviewer:
    async def test_parses_question_branch(self) -> None:
        provider = ScriptedProvider(response=make_text_response(_QUESTION_JSON))
        decision = await _interviewer(provider).run_turn(
            _history(), project_id=None, currency="USD"
        )
        assert decision.needs_more is True
        assert decision.next_question == "What is the budget?"
        assert decision.draft is None

    async def test_parses_draft_branch(self) -> None:
        provider = ScriptedProvider(response=make_text_response(_DRAFT_JSON))
        decision = await _interviewer(provider).run_turn(
            _history(), project_id=None, currency="USD"
        )
        assert decision.needs_more is False
        assert decision.draft is not None
        assert decision.draft.proposed_project_name == "memory-layer"
        assert decision.draft.envelope.amount == pytest.approx(5000.0)

    async def test_malformed_json_raises(self) -> None:
        provider = ScriptedProvider(response=make_text_response("not json at all"))
        with pytest.raises(CharterInterviewResponseInvalidError):
            await _interviewer(provider).run_turn(
                _history(), project_id=None, currency="USD"
            )

    async def test_schema_violation_raises(self) -> None:
        # needs_more true but no next_question violates the XOR contract.
        bad = '{"needs_more": true, "next_question": null, "draft": null}'
        provider = ScriptedProvider(response=make_text_response(bad))
        with pytest.raises(CharterInterviewResponseInvalidError):
            await _interviewer(provider).run_turn(
                _history(), project_id=None, currency="USD"
            )

    async def test_uses_configured_model(self) -> None:
        provider = ScriptedProvider(response=make_text_response(_QUESTION_JSON))
        config = CharterConfig(interview_model=NotBlankStr("example-medium-001"))
        interviewer = LLMCharterInterviewer(provider=provider, config=config)
        await interviewer.run_turn(_history(), project_id=None, currency="USD")
        assert provider.complete_calls[0][1] == "example-medium-001"
