# module-kind: tests
"""Projection of a loop's execution result onto the A/B rubric's inputs.

The rubric ranks loops on tokens, wall-clock, turn efficiency and rework. Every
one of those is already recorded by the loops themselves, so this projection
reads them off ``ExecutionResult`` rather than re-deriving or estimating any of
them. The tests pin that: a metric the loops record must arrive intact, and a
signal only some loops emit must not advantage the loops that cannot emit it.
"""

from datetime import date
from uuid import UUID

import pytest

from evals.runner.metrics import RunMetrics, run_metrics
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.completion_enums import FinishReason
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.execution.turn import TurnRecord

pytestmark = pytest.mark.unit

_AGENT_ID = UUID("00000000-0000-4000-8000-00000000ab01")


def _identity() -> AgentIdentity:
    """A vendor-agnostic identity for building a context."""
    return AgentIdentity(
        id=_AGENT_ID,
        name="A/B Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="example-provider", model_id="example-large-001"),
        hiring_date=date(2026, 1, 1),
    )


def _turn(
    number: int,
    *,
    input_tokens: int = 10,
    output_tokens: int = 5,
    tools: tuple[str, ...] = (),
    fingerprints: tuple[str, ...] = (),
    retry_count: int = 0,
    cache_hit: bool = False,
) -> TurnRecord:
    """Build one turn record with the fields the rubric consumes."""
    return TurnRecord(
        turn_number=number,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=0.01,
        tool_calls_made=tools,
        tool_call_fingerprints=fingerprints,
        finish_reason=FinishReason.STOP,
        retry_count=retry_count,
        cache_hit=cache_hit,
    )


def _result(
    *,
    turns: tuple[TurnRecord, ...],
    metadata: dict[str, object] | None = None,
) -> ExecutionResult:
    """Build an execution result carrying *turns* and optional loop metadata."""
    return ExecutionResult(
        context=AgentContext.from_identity(_identity()),
        termination_reason=TerminationReason.COMPLETED,
        turns=turns,
        metadata=metadata or {},
    )


def test_tokens_and_turns_are_summed_from_the_recorded_turns() -> None:
    """Token totals are read off the loop's own records, never estimated."""
    result = _result(
        turns=(
            _turn(1, input_tokens=100, output_tokens=20),
            _turn(2, input_tokens=250, output_tokens=30),
        )
    )

    metrics = run_metrics(result, duration_seconds=12.5)

    assert metrics.total_turns == 2
    assert metrics.input_tokens == 350
    assert metrics.output_tokens == 50
    assert metrics.total_tokens == 400
    assert metrics.duration_seconds == 12.5


def test_tool_call_profile_preserves_every_call_in_order() -> None:
    """The tool-call profile is a run's shape, so order and repeats matter."""
    result = _result(
        turns=(
            _turn(1, tools=("read_file", "edit_file")),
            _turn(2, tools=("shell_command",)),
            _turn(3, tools=("read_file",)),
        )
    )

    metrics = run_metrics(result, duration_seconds=1.0)

    assert metrics.total_tool_calls == 4
    assert metrics.tool_call_names == (
        "read_file",
        "edit_file",
        "shell_command",
        "read_file",
    )


def test_provider_retries_and_cache_hits_are_counted() -> None:
    """Retries feed the rework signal; cache hits explain a cheap token count."""
    result = _result(
        turns=(
            _turn(1, retry_count=2, cache_hit=True),
            _turn(2, retry_count=1),
            _turn(3, cache_hit=True),
        )
    )

    metrics = run_metrics(result, duration_seconds=1.0)

    assert metrics.provider_retries == 3
    assert metrics.cache_hits == 2


def test_repeated_tool_calls_count_only_the_excess() -> None:
    """Thrash is re-issuing the *same* call, so only duplicates beyond the first count.

    Same measure the stagnation detector uses: a fingerprint seen three times
    contributes two, and a run of all-distinct calls contributes nothing.
    """
    result = _result(
        turns=(
            _turn(1, fingerprints=("read:aaa", "edit:bbb")),
            _turn(2, fingerprints=("read:aaa",)),
            _turn(3, fingerprints=("read:aaa", "shell:ccc")),
        )
    )

    assert run_metrics(result, duration_seconds=1.0).repeated_tool_calls == 2


def test_distinct_tool_calls_are_not_counted_as_thrash() -> None:
    """A loop making many different calls is working, not thrashing."""
    result = _result(
        turns=(_turn(1, fingerprints=("read:aaa", "edit:bbb", "shell:ccc")),)
    )

    assert run_metrics(result, duration_seconds=1.0).repeated_tool_calls == 0


def test_replans_are_read_from_loop_metadata() -> None:
    """plan_execute and hybrid stash their replan count in result metadata."""
    result = _result(turns=(_turn(1),), metadata={"replans_used": 2})

    assert run_metrics(result, duration_seconds=1.0).replans_used == 2


def test_a_loop_that_cannot_replan_reports_zero_replans() -> None:
    """react and openhands emit no replan metadata; absence is zero, not unknown.

    This must never read as an advantage: zero replans is the same value a
    planning loop reports when it needed no replan, and the rubric treats
    replans strictly as a rework cost.
    """
    assert (
        run_metrics(_result(turns=(_turn(1),)), duration_seconds=1.0).replans_used == 0
    )


def test_non_integer_replan_metadata_is_refused() -> None:
    """``metadata`` is untyped, so a drifted value must not silently score as zero."""
    result = _result(turns=(_turn(1),), metadata={"replans_used": "two"})

    with pytest.raises(ValueError, match="replans_used"):
        run_metrics(result, duration_seconds=1.0)


def test_a_run_with_no_turns_projects_to_zeroes() -> None:
    """A loop that terminated before any turn is scoreable, not a crash."""
    metrics = run_metrics(_result(turns=()), duration_seconds=0.0)

    assert metrics == RunMetrics(
        total_turns=0,
        duration_seconds=0.0,
        input_tokens=0,
        output_tokens=0,
        total_tool_calls=0,
        tool_call_names=(),
        repeated_tool_calls=0,
        provider_retries=0,
        cache_hits=0,
        replans_used=0,
    )
