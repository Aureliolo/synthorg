"""Tests for BehaviorTag enum and TurnRecord behavior extensions."""

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.execution.turn import BehaviorTag, TurnRecord


@pytest.mark.unit
class TestBehaviorTag:
    """BehaviorTag enum values and serialization."""

    def test_values(self) -> None:
        assert BehaviorTag.FILE_OPERATIONS.value == "file_operations"
        assert BehaviorTag.RETRIEVAL.value == "retrieval"
        assert BehaviorTag.TOOL_USE.value == "tool_use"
        assert BehaviorTag.MEMORY.value == "memory"
        assert BehaviorTag.CONVERSATION.value == "conversation"
        assert BehaviorTag.SUMMARIZATION.value == "summarization"
        assert BehaviorTag.DELEGATION.value == "delegation"
        assert BehaviorTag.COORDINATION.value == "coordination"
        assert BehaviorTag.VERIFICATION.value == "verification"

    def test_member_count(self) -> None:
        assert len(BehaviorTag) == 9

    def test_str_enum(self) -> None:
        assert str(BehaviorTag.FILE_OPERATIONS) == "file_operations"
        assert isinstance(BehaviorTag.FILE_OPERATIONS, str)

    def test_serialization_roundtrip(self) -> None:
        tag = BehaviorTag.RETRIEVAL
        assert BehaviorTag(tag.value) is tag


@pytest.mark.unit
class TestTurnRecordBehaviorExtensions:
    """TurnRecord behavior_tags and related fields."""

    def _make_turn(self, **overrides: object) -> TurnRecord:
        defaults: dict[str, object] = {
            "turn_number": 1,
            "input_tokens": 100,
            "output_tokens": 50,
            "cost": 0.01,
            "finish_reason": FinishReason.STOP,
        }
        defaults.update(overrides)
        return TurnRecord.model_validate(defaults)

    def test_behavior_tags_default_empty(self) -> None:
        turn = self._make_turn()
        assert turn.behavior_tags == ()

    def test_behavior_tags_with_values(self) -> None:
        turn = self._make_turn(
            behavior_tags=(BehaviorTag.FILE_OPERATIONS, BehaviorTag.RETRIEVAL),
        )
        assert turn.behavior_tags == (
            BehaviorTag.FILE_OPERATIONS,
            BehaviorTag.RETRIEVAL,
        )

    def test_efficiency_delta_default_none(self) -> None:
        turn = self._make_turn()
        assert turn.efficiency_delta is None

    def test_prior_tool_call_count_default_zero(self) -> None:
        turn = self._make_turn()
        assert turn.prior_tool_call_count == 0

    def test_prior_tool_call_count_with_value(self) -> None:
        turn = self._make_turn(prior_tool_call_count=5)
        assert turn.prior_tool_call_count == 5

    def test_tool_response_tokens_default_zero(self) -> None:
        turn = self._make_turn()
        assert turn.tool_response_tokens == 0

    def test_tool_response_tokens_with_value(self) -> None:
        turn = self._make_turn(tool_response_tokens=200)
        assert turn.tool_response_tokens == 200
