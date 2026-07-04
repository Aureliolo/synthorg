"""Unit tests for conversation-transcript rendering and windowing."""

from datetime import UTC, datetime

import pytest

from synthorg.communication.conversation.enums import ConversationRole
from synthorg.core.types import NotBlankStr
from synthorg.engine.token_estimation import DefaultTokenEstimator
from synthorg.meta.chief_of_staff.models import ConversationTurn
from synthorg.meta.chief_of_staff.transcript import (
    window_turns,
    windowed_transcript,
)
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 1, tzinfo=UTC)


def _turn(
    seq: int, content: str, role: ConversationRole = ConversationRole.USER
) -> ConversationTurn:
    """Build a conversation turn for the transcript tests."""
    return ConversationTurn(
        id=as_uuid(f"t{seq}"),
        conversation_id=sid("c1"),
        sequence=seq,
        role=role,
        content=NotBlankStr(content),
        created_at=_NOW,
    )


class TestWindowTurns:
    def test_empty_input_yields_empty_output(self) -> None:
        est = DefaultTokenEstimator()
        assert window_turns((), token_budget=100, estimator=est) == ()
        assert windowed_transcript((), token_budget=100, estimator=est) == ""

    def test_keeps_only_the_recent_suffix_within_budget(self) -> None:
        # Each rendered line "USER: message number {i} " + 20 'x' is 43
        # chars, i.e. 10 estimated tokens; a 30-token budget keeps 3.
        turns = tuple(_turn(i, f"message number {i} " + "x" * 20) for i in range(10))
        kept = window_turns(turns, token_budget=30, estimator=DefaultTokenEstimator())
        assert kept == turns[-3:]
        assert kept[-1] is turns[-1]

    def test_newest_turn_survives_even_when_alone_over_budget(self) -> None:
        turns = (_turn(0, "x" * 4000),)
        kept = window_turns(turns, token_budget=1, estimator=DefaultTokenEstimator())
        assert kept == turns

    def test_all_turns_kept_when_budget_is_ample(self) -> None:
        turns = tuple(_turn(i, f"short {i}") for i in range(5))
        kept = window_turns(
            turns, token_budget=100_000, estimator=DefaultTokenEstimator()
        )
        assert kept == turns

    def test_windowed_transcript_renders_role_prefixed_lines(self) -> None:
        turns = (
            _turn(0, "hello there", ConversationRole.USER),
            _turn(1, "hi back", ConversationRole.ASSISTANT),
        )
        out = windowed_transcript(
            turns, token_budget=1000, estimator=DefaultTokenEstimator()
        )
        assert out == "USER: hello there\nASSISTANT: hi back"
