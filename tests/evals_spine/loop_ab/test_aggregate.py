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
    LoopRepetitionSummary,
    RepetitionOutcome,
    summarise_repetitions,
)
from evals.runner.metrics import RunMetrics
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit


def _metrics(
    *,
    total_turns: int = 5,
    duration_seconds: float = 10.0,
    input_tokens: int = 800,
    output_tokens: int = 200,
    repeated_tool_calls: int = 0,
    provider_retries: int | None = 0,
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
    )


def _outcome(
    *,
    correctness: int = 100,
    passed: bool = True,
    metrics: RunMetrics | None = None,
    termination_reason: str = "completed",
    artifacts_produced: bool = True,
    governance_events: dict[str, int] | None = None,
) -> RepetitionOutcome:
    """Build one recorded repetition."""
    return RepetitionOutcome(
        correctness=correctness,
        passed=passed,
        termination_reason=NotBlankStr(termination_reason),
        artifacts_produced=artifacts_produced,
        governance_events=governance_events or {},
        metrics=metrics or _metrics(),
    )


def _summarise(
    *, loop_type: str, outcomes: tuple[RepetitionOutcome, ...]
) -> LoopRepetitionSummary:
    """Summarise a cell that ran every repetition it planned.

    Returns:
        The reduced summary.
    """
    return summarise_repetitions(
        loop_type=loop_type, outcomes=outcomes, planned=len(outcomes)
    )


class TestOutcomeReporting:
    def test_every_termination_reason_is_counted(self) -> None:
        # A run that ends NO_OP produced nothing for a task that expected
        # something, which is a different failure from an error and from a turn
        # ceiling. A single pass rate cannot tell the three apart, and which one
        # a loop keeps hitting is the decision-relevant part.
        summary = _summarise(
            loop_type="react",
            outcomes=(
                _outcome(termination_reason="completed"),
                _outcome(termination_reason="no_op", passed=False),
                _outcome(termination_reason="no_op", passed=False),
            ),
        )

        assert summary.termination_reasons == {"completed": 1, "no_op": 2}

    def test_the_produced_artifact_rate_is_a_rate(self) -> None:
        summary = _summarise(
            loop_type="react",
            outcomes=(
                _outcome(artifacts_produced=True),
                _outcome(artifacts_produced=False),
                _outcome(artifacts_produced=False),
                _outcome(artifacts_produced=False),
            ),
        )

        assert summary.artifact_rate == pytest.approx(0.25)

    def test_governance_events_sum_across_repetitions(self) -> None:
        # Reported, never scored. A loop that trips the turn ceiling twice as
        # often is telling the operator something the composite already prices
        # in through correctness and turns; double-counting it in the ranking
        # would weight one behaviour twice.
        summary = _summarise(
            loop_type="react",
            outcomes=(
                _outcome(governance_events={"execution.max_turns_exceeded": 1}),
                _outcome(
                    governance_events={
                        "execution.max_turns_exceeded": 1,
                        "stagnation.detected": 2,
                    }
                ),
            ),
        )

        assert summary.governance_events == {
            "execution.max_turns_exceeded": 2,
            "stagnation.detected": 2,
        }

    def test_a_clean_run_reports_no_governance_events(self) -> None:
        summary = _summarise(loop_type="react", outcomes=(_outcome(),))

        assert summary.governance_events == {}


def test_continuous_measures_reduce_to_the_median() -> None:
    """The median resists a single outlier run in a way the mean does not."""
    summary = _summarise(
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
    summary = _summarise(
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
    summary = _summarise(
        loop_type="openhands",
        outcomes=(
            _outcome(passed=True),
            _outcome(passed=False, correctness=0),
            _outcome(passed=True),
        ),
    )

    assert summary.aggregate.pass_rate == pytest.approx(2.0 / 3.0)


def test_rework_counts_every_kind_of_redone_work() -> None:
    """Retries and repeated tool calls are both work done twice."""
    summary = _summarise(
        loop_type="react",
        outcomes=(
            _outcome(metrics=_metrics(provider_retries=1, repeated_tool_calls=3)),
        ),
    )

    assert summary.aggregate.rework_events == pytest.approx(4.0)


def test_unobservable_retries_survive_reduction_as_unobservable() -> None:
    """A loop that measures no retries must not reduce to having had none."""
    summary = _summarise(
        loop_type="openhands",
        outcomes=(
            _outcome(metrics=_metrics(provider_retries=None, repeated_tool_calls=2)),
            _outcome(metrics=_metrics(provider_retries=None, repeated_tool_calls=4)),
        ),
    )

    assert summary.aggregate.provider_retries is None
    assert summary.aggregate.repeated_tool_calls == pytest.approx(3.0)


def test_a_partly_measured_retry_count_reduces_to_unobservable() -> None:
    """One unmeasured repetition makes the loop's retry median unreportable."""
    summary = _summarise(
        loop_type="react",
        outcomes=(
            _outcome(metrics=_metrics(provider_retries=2)),
            _outcome(metrics=_metrics(provider_retries=None)),
        ),
    )

    assert summary.aggregate.provider_retries is None


def test_the_correctness_spread_is_reported_not_discarded() -> None:
    """Two loops can share a median while differing wildly in consistency."""
    summary = _summarise(
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
    summary = _summarise(
        loop_type="react",
        outcomes=(_outcome(correctness=90), _outcome(correctness=90)),
    )
    spread = summary.correctness_spread

    assert spread.minimum == spread.median == spread.maximum == pytest.approx(90.0)


def test_the_repetition_count_is_carried_into_the_summary() -> None:
    """A reader must be able to see how much evidence backs a row."""
    summary = _summarise(
        loop_type="react", outcomes=(_outcome(), _outcome(), _outcome())
    )

    assert summary.repetitions == 3


def test_the_loop_type_is_preserved_onto_the_aggregate() -> None:
    """The aggregate is the rubric's input, so it must name its own loop."""
    summary = _summarise(loop_type="openhands", outcomes=(_outcome(),))

    assert summary.aggregate.loop_type == "openhands"


def test_summarising_no_repetitions_is_refused() -> None:
    """Zero recorded runs is a broken recording, not a zero-scoring loop."""
    with pytest.raises(ValueError, match="at least one"):
        summarise_repetitions(loop_type="react", outcomes=(), planned=3)


def test_a_cell_that_lost_a_repetition_says_so() -> None:
    """Fewer runs than planned is a weaker measurement, and has to look like one.

    Without the planned count a cell whose last repetition failed is
    indistinguishable from a manifest that only ever asked for two.
    """
    summary = summarise_repetitions(
        loop_type="react", outcomes=(_outcome(), _outcome()), planned=3
    )

    assert summary.repetitions == 2
    assert summary.repetitions_planned == 3
    assert summary.is_partial


def test_a_complete_cell_is_not_partial() -> None:
    summary = summarise_repetitions(
        loop_type="react", outcomes=(_outcome(), _outcome()), planned=2
    )

    assert not summary.is_partial


def test_more_runs_than_planned_is_refused() -> None:
    """The two counts describe the same cell, so they cannot disagree that way."""
    with pytest.raises(ValueError, match="planned"):
        summarise_repetitions(
            loop_type="react", outcomes=(_outcome(), _outcome()), planned=1
        )
