# module-kind: tests
"""Reduction of a loop's repeated runs into one scoreable aggregate.

Repetitions exist so a single unlucky run cannot flip a promotion decision. The
reduction is therefore a median rather than a mean, and the spread is carried
into the report rather than discarded: a loop whose runs disagree wildly is a
different proposition from one that lands the same result every time, even when
their medians match.
"""

import pytest

from evals.loop_ab.aggregate import (
    RepetitionOutcome,
    summarise_repetitions,
)
from evals.runner.metrics import RunMetrics
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit


def _metrics(  # noqa: PLR0913 -- orthogonal per-run measurements
    *,
    total_turns: int = 5,
    duration_seconds: float = 10.0,
    input_tokens: int = 800,
    output_tokens: int = 200,
    repeated_tool_calls: int = 0,
    provider_retries: int = 0,
    replans_used: int = 0,
) -> RunMetrics:
    """Build one run's metrics."""
    return RunMetrics(
        total_turns=total_turns,
        duration_seconds=duration_seconds,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tool_calls=0,
        tool_call_names=(),
        repeated_tool_calls=repeated_tool_calls,
        provider_retries=provider_retries,
        cache_hits=0,
        replans_used=replans_used,
    )


def _outcome(
    *, correctness: int = 100, passed: bool = True, metrics: RunMetrics | None = None
) -> RepetitionOutcome:
    """Build one recorded repetition."""
    return RepetitionOutcome(
        correctness=correctness,
        passed=passed,
        termination_reason=NotBlankStr("completed"),
        metrics=metrics or _metrics(),
    )


def test_continuous_measures_reduce_to_the_median() -> None:
    """The median resists a single outlier run in a way the mean does not."""
    summary = summarise_repetitions(
        loop_type="react",
        outcomes=(
            _outcome(metrics=_metrics(total_turns=4, duration_seconds=8.0)),
            _outcome(metrics=_metrics(total_turns=5, duration_seconds=10.0)),
            _outcome(metrics=_metrics(total_turns=90, duration_seconds=900.0)),
        ),
    )

    assert summary.aggregate.total_turns == pytest.approx(5.0)
    assert summary.aggregate.duration_seconds == pytest.approx(10.0)


def test_tokens_are_summed_per_run_then_reduced() -> None:
    """The rubric ranks on the provider-neutral total, not the split."""
    summary = summarise_repetitions(
        loop_type="react",
        outcomes=(
            _outcome(metrics=_metrics(input_tokens=100, output_tokens=50)),
            _outcome(metrics=_metrics(input_tokens=300, output_tokens=100)),
            _outcome(metrics=_metrics(input_tokens=1_000, output_tokens=500)),
        ),
    )

    assert summary.aggregate.total_tokens == pytest.approx(400.0)


def test_pass_rate_is_the_fraction_of_runs_that_landed() -> None:
    """Reliability across repetitions is half the resilience signal."""
    summary = summarise_repetitions(
        loop_type="hybrid",
        outcomes=(
            _outcome(passed=True),
            _outcome(passed=False, correctness=0),
            _outcome(passed=True),
        ),
    )

    assert summary.aggregate.pass_rate == pytest.approx(2.0 / 3.0)


def test_rework_counts_every_kind_of_redone_work() -> None:
    """Retries, replans and repeated tool calls are all work done twice."""
    summary = summarise_repetitions(
        loop_type="hybrid",
        outcomes=(
            _outcome(
                metrics=_metrics(
                    provider_retries=1, replans_used=2, repeated_tool_calls=3
                )
            ),
        ),
    )

    assert summary.aggregate.rework_events == pytest.approx(6.0)


def test_the_correctness_spread_is_reported_not_discarded() -> None:
    """Two loops can share a median while differing wildly in consistency."""
    summary = summarise_repetitions(
        loop_type="react",
        outcomes=(
            _outcome(correctness=20),
            _outcome(correctness=80),
            _outcome(correctness=100),
        ),
    )

    assert summary.correctness_spread.minimum == pytest.approx(20.0)
    assert summary.correctness_spread.median == pytest.approx(80.0)
    assert summary.correctness_spread.maximum == pytest.approx(100.0)


def test_a_consistent_loop_reports_a_zero_width_spread() -> None:
    """Identical runs collapse the spread, which is itself the signal."""
    summary = summarise_repetitions(
        loop_type="react",
        outcomes=(_outcome(correctness=90), _outcome(correctness=90)),
    )
    spread = summary.correctness_spread

    assert spread.minimum == spread.median == spread.maximum == pytest.approx(90.0)


def test_the_repetition_count_is_carried_into_the_summary() -> None:
    """A reader must be able to see how much evidence backs a row."""
    summary = summarise_repetitions(
        loop_type="react", outcomes=(_outcome(), _outcome(), _outcome())
    )

    assert summary.repetitions == 3


def test_the_loop_type_is_preserved_onto_the_aggregate() -> None:
    """The aggregate is the rubric's input, so it must name its own loop."""
    summary = summarise_repetitions(loop_type="openhands", outcomes=(_outcome(),))

    assert summary.aggregate.loop_type == "openhands"


def test_summarising_no_repetitions_is_refused() -> None:
    """Zero recorded runs is a broken recording, not a zero-scoring loop."""
    with pytest.raises(ValueError, match="at least one"):
        summarise_repetitions(loop_type="react", outcomes=())
