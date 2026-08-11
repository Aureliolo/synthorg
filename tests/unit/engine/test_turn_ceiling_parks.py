"""Running out of turns is not failing.

A ceiling is a backstop against a pathological loop, not a verdict on work
that is taking longer than the estimate. So a run that reaches one and is
still doing something takes a further budget a bounded number of times, and
parks with its work intact once they are spent, rather than being failed and
torn down. A run doing nothing gets no extension.
"""

from datetime import date

import pytest
import structlog.testing

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.completion_enums import FinishReason
from synthorg.engine.context import DEFAULT_MAX_TURN_EXTENSIONS, AgentContext
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.loop_turn_budget import (
    TURN_CEILING_METADATA_KEY,
    ceiling_result,
    grant_extension,
    restore_turn_budget,
)
from synthorg.execution.turn import TurnRecord
from synthorg.observability.events.execution import EXECUTION_MAX_TURNS_EXCEEDED

pytestmark = pytest.mark.unit


def _ctx(**overrides: object) -> AgentContext:
    identity = AgentIdentity(
        name="Ceiling Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
    )
    # Extensions are asked for, never inherited: a context that did not
    # request them ends at its first ceiling, so the run under test says so.
    ctx = AgentContext.from_identity(
        identity,
        max_turns=20,
        turn_extensions=DEFAULT_MAX_TURN_EXTENSIONS,
    )
    return ctx.model_copy(update=overrides) if overrides else ctx


def _turns(count: int, *, working: bool = True) -> list[TurnRecord]:
    """Build *count* turn records, each calling a tool when *working*.

    Returns:
        The recorded turns.
    """
    return [
        TurnRecord(
            turn_number=number,
            input_tokens=1,
            output_tokens=1,
            cost=0.0,
            tool_calls_made=("write_file",) if working else (),
            finish_reason=FinishReason.STOP,
        )
        for number in range(1, count + 1)
    ]


class TestGrantExtension:
    def test_a_fresh_run_may_extend(self) -> None:
        extended = grant_extension(_ctx(), _turns(20))

        assert extended is not None
        assert extended.max_turns > 20
        assert extended.turn_extensions_remaining == DEFAULT_MAX_TURN_EXTENSIONS - 1
        assert extended.turn_extensions_granted == 1

    def test_extensions_run_out(self) -> None:
        ctx = _ctx(turn_extensions_remaining=0)

        assert grant_extension(ctx, _turns(20)) is None

    def test_a_run_doing_nothing_earns_nothing(self) -> None:
        """Otherwise the default allowance quadruples a pathological loop."""
        assert grant_extension(_ctx(), _turns(20, working=False)) is None

    def test_only_the_budget_just_spent_counts(self) -> None:
        """Work done before the previous extension is not fresh progress."""
        ctx = _ctx(max_turns=40, turn_extensions_granted=1)
        turns = _turns(20) + _turns(20, working=False)

        assert grant_extension(ctx, turns) is None

    def test_each_extension_is_recorded(self) -> None:
        ctx = _ctx()
        granted = 0
        while (extended := grant_extension(ctx, _turns(ctx.max_turns))) is not None:
            ctx = extended
            granted += 1

        assert granted == DEFAULT_MAX_TURN_EXTENSIONS
        assert ctx.turn_extensions_granted == DEFAULT_MAX_TURN_EXTENSIONS

    def test_headroom_only_ever_grows(self) -> None:
        """A later extension must never shrink the budget it extends."""
        ctx = _ctx()
        seen = [ctx.max_turns]
        while (extended := grant_extension(ctx, _turns(ctx.max_turns))) is not None:
            ctx = extended
            seen.append(ctx.max_turns)

        assert seen == sorted(seen)
        assert len(set(seen)) == len(seen)

    def test_every_extension_is_worth_the_configured_budget(self) -> None:
        ctx = _ctx()
        seen = [ctx.max_turns]
        while (extended := grant_extension(ctx, _turns(ctx.max_turns))) is not None:
            ctx = extended
            seen.append(ctx.max_turns)

        assert seen == [20, 40, 60, 80]


class TestCeilingResult:
    def test_a_run_that_took_extensions_parks(self) -> None:
        ctx = _ctx(turn_extensions_remaining=0, turn_extensions_granted=3)

        result = ceiling_result(ctx, [])

        assert result.termination_reason is TerminationReason.PARKED
        assert result.metadata[TURN_CEILING_METADATA_KEY] is True

    def test_extensions_disabled_still_ends_the_run(self) -> None:
        """Zero extensions is an operator asking for the old behaviour."""
        ctx = _ctx(turn_extensions_remaining=0, turn_extensions_granted=0)

        result = ceiling_result(ctx, [])

        assert result.termination_reason is TerminationReason.MAX_TURNS
        assert TURN_CEILING_METADATA_KEY not in result.metadata

    def test_the_park_is_distinguishable_from_a_clarification(self) -> None:
        """The sync layer asks a different question for each."""
        ctx = _ctx(turn_extensions_remaining=0, turn_extensions_granted=1)

        result = ceiling_result(ctx, [])

        assert result.metadata.get("clarification") is None
        assert result.metadata.get("decision") is None

    @pytest.mark.parametrize("granted", [0, 3])
    def test_a_spent_budget_names_itself(self, granted: int) -> None:
        """Both outcomes emit the fact the scorers key on.

        ``AgentContext.with_turn_completed`` raises past the ceiling, so a loop
        that checks ``has_turns_remaining`` first never reaches its log. That is
        every loop, which left the event unreachable from a real run and the
        penalty tables keyed on it inert.
        """
        ctx = _ctx(turn_extensions_remaining=0, turn_extensions_granted=granted)

        with structlog.testing.capture_logs() as logs:
            ceiling_result(ctx, _turns(20))

        assert EXECUTION_MAX_TURNS_EXCEEDED in [entry["event"] for entry in logs]


class TestRestoreTurnBudget:
    """A resumed run must have somewhere to run."""

    def test_a_run_with_turns_left_is_untouched(self) -> None:
        ctx = _ctx(turn_count=3)

        assert restore_turn_budget(ctx, approved=True, extensions=3) is ctx

    def test_approval_hands_back_a_budget_and_the_allowance(self) -> None:
        spent = _ctx(
            max_turns=80,
            turn_count=80,
            turn_extensions_remaining=0,
            turn_extensions_granted=3,
        )

        resumed = restore_turn_budget(spent, approved=True, extensions=3)

        # 80 is four budgets of 20; the fifth takes it to 100.
        assert resumed.max_turns == 100
        assert resumed.turn_extensions_remaining == 3
        assert resumed.turn_extensions_granted == 4

    def test_the_resumed_run_can_extend_by_the_same_budget_again(self) -> None:
        """The grant arithmetic survives the round trip through a park."""
        spent = _ctx(
            max_turns=80,
            turn_count=80,
            turn_extensions_remaining=0,
            turn_extensions_granted=3,
        )

        resumed = restore_turn_budget(spent, approved=True, extensions=3)
        extended = grant_extension(resumed, _turns(20))

        assert extended is not None
        assert extended.max_turns == 120

    def test_rejection_hands_back_a_budget_but_no_allowance(self) -> None:
        """The run may finish its sentence; it may not ask again."""
        spent = _ctx(
            max_turns=80,
            turn_count=80,
            turn_extensions_remaining=0,
            turn_extensions_granted=3,
        )

        resumed = restore_turn_budget(spent, approved=False, extensions=3)

        assert resumed.max_turns == 100
        assert resumed.turn_extensions_remaining == 0
        assert grant_extension(resumed, _turns(20)) is None
        # Nothing granted means the next ceiling ends the run rather than
        # raising the same question a second time.
        assert (
            ceiling_result(resumed, []).termination_reason
            is TerminationReason.MAX_TURNS
        )
