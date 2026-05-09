"""Behavioural eval: HealthJudge ticket emission.

HealthJudge is heuristic, not LLM-backed, so a "prompt eval" doesn't
apply. The corresponding deterministic-property eval is a labelled
behavioural set: ``(termination_reason / quality_signals / has_recovery)
-> expected EscalationCause | None``. Any heuristic regression that
flips a labelled outcome fails the suite.

Reference implementation: ``synthorg.engine.health.judge.HealthJudge``.
"""

import pytest

from synthorg.engine.health.judge import HealthJudge
from synthorg.engine.health.models import EscalationCause
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.quality.models import StepQuality, StepQualitySignal
from tests.evals.prompt._harness import (
    LabelledExample,
    assert_accuracy_at_least,
    run_grader,
)


def _signal(quality: StepQuality, *, step_index: int = 0) -> StepQualitySignal:
    return StepQualitySignal(
        quality=quality,
        confidence=0.9,
        reason=f"eval-{quality.value}",
        step_index=step_index,
        turn_range=(1, 1),
    )


_INCORRECT = _signal(StepQuality.INCORRECT)
_CORRECT = _signal(StepQuality.CORRECT)


@pytest.mark.unit
class TestHealthJudgeBehaviour:
    """Labelled-input behavioural eval for HealthJudge.emit_ticket."""

    EXAMPLES: tuple[LabelledExample, ...] = (
        LabelledExample(
            name="stagnation_emits_stagnation",
            inp={
                "termination_reason": TerminationReason.STAGNATION,
                "has_recovery": False,
                "quality_signals": (),
            },
            expected=EscalationCause.STAGNATION,
        ),
        LabelledExample(
            name="error_with_recovery_emits_repeated_failure",
            inp={
                "termination_reason": TerminationReason.ERROR,
                "has_recovery": True,
                "quality_signals": (),
            },
            expected=EscalationCause.REPEATED_FAILURE,
        ),
        LabelledExample(
            name="error_without_recovery_emits_nothing",
            inp={
                "termination_reason": TerminationReason.ERROR,
                "has_recovery": False,
                "quality_signals": (),
            },
            expected=None,
        ),
        LabelledExample(
            name="three_trailing_incorrect_emits_quality_degradation",
            inp={
                "termination_reason": TerminationReason.COMPLETED,
                "has_recovery": False,
                "quality_signals": (_INCORRECT, _INCORRECT, _INCORRECT),
            },
            expected=EscalationCause.QUALITY_DEGRADATION,
        ),
        LabelledExample(
            name="two_trailing_incorrect_below_threshold",
            inp={
                "termination_reason": TerminationReason.COMPLETED,
                "has_recovery": False,
                "quality_signals": (_INCORRECT, _INCORRECT),
            },
            expected=None,
        ),
        LabelledExample(
            name="trailing_correct_breaks_streak",
            inp={
                "termination_reason": TerminationReason.COMPLETED,
                "has_recovery": False,
                "quality_signals": (_INCORRECT, _INCORRECT, _INCORRECT, _CORRECT),
            },
            expected=None,
        ),
        LabelledExample(
            name="completed_clean_signals_emits_nothing",
            inp={
                "termination_reason": TerminationReason.COMPLETED,
                "has_recovery": False,
                "quality_signals": (_CORRECT, _CORRECT),
            },
            expected=None,
        ),
        LabelledExample(
            name="six_trailing_incorrect_escalates_to_critical",
            inp={
                "termination_reason": TerminationReason.COMPLETED,
                "has_recovery": False,
                "quality_signals": (_INCORRECT,) * 6,
            },
            # The cause is still QUALITY_DEGRADATION; the ``critical``
            # severity is checked separately in
            # ``test_critical_severity_at_double_threshold`` below so
            # this labelled set stays uniform.
            expected=EscalationCause.QUALITY_DEGRADATION,
        ),
    )

    def test_emit_ticket_matches_labelled_examples(self) -> None:
        """Every labelled (input, expected_cause) pair grades correctly."""
        judge = HealthJudge()

        def _grade(actual_input: object, expected: object) -> bool:
            assert isinstance(actual_input, dict)
            ticket = judge.emit_ticket(
                termination_reason=actual_input["termination_reason"],
                has_recovery=actual_input["has_recovery"],
                quality_signals=actual_input["quality_signals"],
                agent_id="agent-eval",
                task_id="task-eval",
            )
            actual_cause = ticket.cause if ticket is not None else None
            return actual_cause == expected

        outcome = run_grader(self.EXAMPLES, _grade)
        # 100% accuracy: every labelled outcome must match. The whole
        # suite exists to catch heuristic regressions, not soft drift.
        assert_accuracy_at_least(outcome, 1.0)

    def test_critical_severity_at_double_threshold(self) -> None:
        """At >= 2x threshold trailing INCORRECT, severity is CRITICAL."""
        from synthorg.engine.health.models import EscalationSeverity

        judge = HealthJudge(quality_degradation_threshold=3)
        ticket = judge.emit_ticket(
            termination_reason=TerminationReason.COMPLETED,
            has_recovery=False,
            quality_signals=(_INCORRECT,) * 6,
            agent_id="agent-eval",
            task_id="task-eval",
        )
        assert ticket is not None
        assert ticket.severity == EscalationSeverity.CRITICAL

    def test_high_severity_below_double_threshold(self) -> None:
        """Just at threshold but below 2x => severity is HIGH."""
        from synthorg.engine.health.models import EscalationSeverity

        judge = HealthJudge(quality_degradation_threshold=3)
        ticket = judge.emit_ticket(
            termination_reason=TerminationReason.COMPLETED,
            has_recovery=False,
            quality_signals=(_INCORRECT,) * 3,
            agent_id="agent-eval",
            task_id="task-eval",
        )
        assert ticket is not None
        assert ticket.severity == EscalationSeverity.HIGH
