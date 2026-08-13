"""Recording, per turn, how many tool calls actually named a tool.

The turn record is built from the model's response, before anything runs, so
by itself it can only say what was asked for. The turn-budget guard and the
unresolved-tool stop both read what RESOLVED, and a live run showed what the
difference is worth: 264 turns and every extension bought on a tool that does
not exist.

The counting itself is a pure function and cheap to test. What had no coverage
is the seam, which is where a wrong argument or a dropped call would put the
number back to what it was.
"""

from typing import Final

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.engine.loop_tool_execution import record_resolved_tool_calls
from synthorg.execution.turn import TurnRecord
from synthorg.providers.models import ToolResult

pytestmark = pytest.mark.unit

_ASKED: Final[tuple[str, ...]] = ("write", "write_file")


def _turn() -> TurnRecord:
    return TurnRecord(
        turn_number=1,
        input_tokens=1,
        output_tokens=1,
        cost=0.0,
        tool_calls_made=_ASKED,
        finish_reason=FinishReason.TOOL_USE,
    )


def _unresolved(call_id: str) -> ToolResult:
    return ToolResult(
        tool_call_id=call_id,
        content="tool 'write' is not registered",
        is_error=True,
        is_unresolved=True,
    )


def _ran(call_id: str, *, failed: bool = False) -> ToolResult:
    return ToolResult(
        tool_call_id=call_id,
        content="done",
        is_error=failed,
    )


class TestRecordResolvedToolCalls:
    def test_a_turn_where_nothing_resolved_records_zero(self) -> None:
        turns = [_turn()]

        record_resolved_tool_calls(turns, [_unresolved("a"), _unresolved("b")])

        assert turns[-1].resolved_tool_calls == 0

    def test_a_failing_tool_still_counts_as_resolved(self) -> None:
        """It ran. A failure is a result, and the next turn can act on it.

        Counting it as unresolved would stop runs that are working through a
        genuine error, which is the opposite of the intent.
        """
        turns = [_turn()]

        record_resolved_tool_calls(turns, [_ran("a", failed=True)])

        assert turns[-1].resolved_tool_calls == 1

    def test_a_mixed_turn_counts_only_what_ran(self) -> None:
        turns = [_turn()]

        record_resolved_tool_calls(turns, [_unresolved("a"), _ran("b")])

        assert turns[-1].resolved_tool_calls == 1

    def test_only_the_latest_turn_is_rewritten(self) -> None:
        """Earlier turns are settled history; the streak is read across them."""
        first = _turn().model_copy(update={"resolved_tool_calls": 2})
        turns = [first, _turn()]

        record_resolved_tool_calls(turns, [_unresolved("a")])

        assert turns[0].resolved_tool_calls == 2
        assert turns[1].resolved_tool_calls == 0

    def test_no_turns_is_not_an_error(self) -> None:
        """Tool results can arrive before any turn is recorded on a resume."""
        turns: list[TurnRecord] = []

        record_resolved_tool_calls(turns, [_ran("a")])

        assert turns == []

    def test_what_was_asked_for_is_left_intact(self) -> None:
        """Both halves are needed: the count decides, the names explain."""
        turns = [_turn()]

        record_resolved_tool_calls(turns, [_unresolved("a"), _unresolved("b")])

        assert turns[-1].tool_calls_made == _ASKED
