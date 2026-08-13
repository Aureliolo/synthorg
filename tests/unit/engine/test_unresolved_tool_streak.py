"""A run asking for tools nobody has registered stops early.

A live run asked for a tool named ``write``, which is not in the registry. The
registry answered by name with its four nearest matches, and the agent asked
for ``write`` again 246 more times, drifting its arguments a few characters
each turn so the fingerprint detector saw no repetition at all. Nothing stopped
it: every one of those turns "called a tool", so it earned a second turn budget
at turn 300 and was on its way to 1200.

The signal that survives drifting arguments is whether anything ran.
"""

from datetime import date

import pytest
import structlog.testing

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.completion_enums import FinishReason
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.loop_unresolved_tools import (
    UNRESOLVED_TOOLS_METADATA_KEY,
    unresolved_streak,
    unresolved_tools_result,
)
from synthorg.execution.turn import TurnRecord
from synthorg.observability.events.execution import EXECUTION_LOOP_TERMINATED

pytestmark = pytest.mark.unit


def _ctx(**overrides: object) -> AgentContext:
    identity = AgentIdentity(
        name="Unresolved Tool Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
    )
    ctx = AgentContext.from_identity(identity)
    return ctx.model_copy(update=overrides) if overrides else ctx


def _turn(number: int, *, asked: tuple[str, ...], resolved: int) -> TurnRecord:
    """Build one turn that asked for *asked* and resolved *resolved* of them.

    Returns:
        The turn record.
    """
    return TurnRecord(
        turn_number=number,
        input_tokens=1,
        output_tokens=1,
        cost=0.0,
        tool_calls_made=asked,
        resolved_tool_calls=resolved,
        finish_reason=FinishReason.TOOL_USE,
    )


def _unresolved(count: int, *, name: str = "write") -> list[TurnRecord]:
    """Build *count* turns that each asked for a tool nobody registered.

    Returns:
        The turn records.
    """
    return [_turn(number, asked=(name,), resolved=0) for number in range(1, count + 1)]


class TestUnresolvedStreak:
    def test_no_turns_is_no_streak(self) -> None:
        assert unresolved_streak([]) == 0

    def test_every_turn_resolving_nothing_is_the_whole_run(self) -> None:
        assert unresolved_streak(_unresolved(7)) == 7

    def test_a_turn_that_ran_something_breaks_the_streak(self) -> None:
        turns = [
            *_unresolved(4),
            _turn(5, asked=("write_file",), resolved=1),
            *_unresolved(2),
        ]

        assert unresolved_streak(turns) == 2

    def test_a_thinking_turn_breaks_the_streak(self) -> None:
        """A turn calling no tool is not this failure; it may be an answer."""
        turns = [*_unresolved(4), _turn(5, asked=(), resolved=0)]

        assert unresolved_streak(turns) == 0

    def test_one_resolved_call_among_several_counts_as_progress(self) -> None:
        turns = [*_unresolved(4), _turn(5, asked=("write", "write_file"), resolved=1)]

        assert unresolved_streak(turns) == 0


class TestUnresolvedToolsResult:
    def test_a_short_streak_is_left_alone(self) -> None:
        assert unresolved_tools_result(_ctx(), _unresolved(4)) is None

    def test_reaching_the_ceiling_stops_the_run(self) -> None:
        result = unresolved_tools_result(_ctx(), _unresolved(5))

        assert result is not None
        assert result.termination_reason is TerminationReason.STAGNATION

    def test_the_result_names_the_tool_it_kept_asking_for(self) -> None:
        """The whole finding is which name it guessed; a count is not enough."""
        result = unresolved_tools_result(_ctx(), _unresolved(5, name="write"))

        assert result is not None
        assert result.metadata[UNRESOLVED_TOOLS_METADATA_KEY] == ["write"]

    def test_every_name_in_the_streak_is_reported(self) -> None:
        """The decision scans the streak, so the finding must describe it.

        Reading the last turn alone names whichever tool the run happened to
        guess last, which is the least informative one: an agent cycling
        through four wrong names looks like an agent that asked for one.
        """
        turns = [
            _turn(1, asked=("write",), resolved=0),
            _turn(2, asked=("edit",), resolved=0),
            _turn(3, asked=("write",), resolved=0),
            _turn(4, asked=("patch_file",), resolved=0),
            _turn(5, asked=("save",), resolved=0),
        ]

        result = unresolved_tools_result(_ctx(), turns)

        assert result is not None
        assert result.metadata[UNRESOLVED_TOOLS_METADATA_KEY] == [
            "edit",
            "patch_file",
            "save",
            "write",
        ]

    def test_names_from_before_the_streak_are_not_reported(self) -> None:
        """A tool that ran is not what the run is being stopped for."""
        turns = [
            _turn(1, asked=("write_file",), resolved=1),
            *_unresolved(5, name="write"),
        ]

        result = unresolved_tools_result(_ctx(), turns)

        assert result is not None
        assert result.metadata[UNRESOLVED_TOOLS_METADATA_KEY] == ["write"]

    def test_the_stop_is_logged_with_the_streak(self) -> None:
        with structlog.testing.capture_logs() as logs:
            unresolved_tools_result(_ctx(), _unresolved(6))

        terminated = [e for e in logs if e["event"] == EXECUTION_LOOP_TERMINATED]
        assert terminated
        assert terminated[0]["unresolved_turns"] == 6

    def test_zero_disables_the_stop(self) -> None:
        """An operator may choose to let the turn ceiling be the only bound."""
        ctx = _ctx(max_unresolved_tool_turns=0)

        assert unresolved_tools_result(ctx, _unresolved(50)) is None

    def test_a_working_run_is_never_stopped(self) -> None:
        turns = [_turn(n, asked=("write_file",), resolved=1) for n in range(1, 51)]

        assert unresolved_tools_result(_ctx(), turns) is None
