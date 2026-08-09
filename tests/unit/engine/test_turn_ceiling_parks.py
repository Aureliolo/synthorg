"""Running out of turns is not failing.

A live run ended four of five build agents at the turn ceiling. Each had
written real files; each was failed, its workspace torn down and its work
discarded. A ceiling is a backstop against a pathological loop, not a verdict
on work that is taking longer than the estimate, so the run now takes further
budgets a bounded number of times and parks with its work intact once they
are spent.
"""

from datetime import date

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.engine.context import DEFAULT_MAX_TURN_EXTENSIONS, AgentContext
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.loop_turn_budget import (
    TURN_CEILING_METADATA_KEY,
    ceiling_result,
    grant_extension,
)

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


class TestGrantExtension:
    def test_a_fresh_run_may_extend(self) -> None:
        extended = grant_extension(_ctx())

        assert extended is not None
        assert extended.max_turns > 20
        assert extended.turn_extensions_remaining == DEFAULT_MAX_TURN_EXTENSIONS - 1
        assert extended.turn_extensions_granted == 1

    def test_extensions_run_out(self) -> None:
        ctx = _ctx(turn_extensions_remaining=0)

        assert grant_extension(ctx) is None

    def test_each_extension_is_recorded(self) -> None:
        ctx = _ctx()
        granted = 0
        while (extended := grant_extension(ctx)) is not None:
            ctx = extended
            granted += 1

        assert granted == DEFAULT_MAX_TURN_EXTENSIONS
        assert ctx.turn_extensions_granted == DEFAULT_MAX_TURN_EXTENSIONS

    def test_headroom_only_ever_grows(self) -> None:
        """A later extension must never shrink the budget it extends."""
        ctx = _ctx()
        seen = [ctx.max_turns]
        while (extended := grant_extension(ctx)) is not None:
            ctx = extended
            seen.append(ctx.max_turns)

        assert seen == sorted(seen)
        assert len(set(seen)) == len(seen)


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
