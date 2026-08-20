"""Tests for the subtask size signal.

The question this answers had no answer at all before: an item the planner
made too coarse was dispatched whole, burned its cap, and either failed or
landed half the work, which the zero-artifact guard does not catch because it
only asks whether the run produced NOTHING.
"""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.atomicity import (
    MAX_SATISFIED_CRITERIA,
    AtomicityVerdict,
    SubtaskAtomicityPolicy,
)
from synthorg.engine.decomposition.models import SubtaskDefinition

pytestmark = pytest.mark.unit

#: The thresholds every case below is judged against, named so a case reads
#: as "one over" rather than as two unrelated integers.
_MAX_ARTIFACTS = 2
_MAX_CRITERIA = 3


def _policy() -> SubtaskAtomicityPolicy:
    """Build the policy the cases are judged under.

    Returns:
        A policy on the fixed test thresholds.
    """
    return SubtaskAtomicityPolicy(
        max_expected_artifacts=_MAX_ARTIFACTS,
        max_acceptance_criteria=_MAX_CRITERIA,
    )


def _subtask(
    *,
    artifacts: int = 1,
    criteria: int = 1,
    satisfies: int = 1,
) -> SubtaskDefinition:
    """Build a definition declaring the given counts.

    Returns:
        The subtask definition.
    """
    return SubtaskDefinition(
        id=NotBlankStr("unit-1"),
        title=NotBlankStr("A unit"),
        description=NotBlankStr("Do the thing"),
        expected_artifacts=tuple(
            NotBlankStr(f"out/file{index}.py") for index in range(artifacts)
        ),
        acceptance_criteria=tuple(
            NotBlankStr(f"criterion {index}") for index in range(criteria)
        ),
        satisfies=tuple(NotBlankStr(f"R{index:02d}") for index in range(satisfies)),
    )


class TestWhatCountsAsOneAgentsWork:
    """The boundaries, from both sides.

    Each threshold is tested AT its limit and one past it, because an
    off-by-one here is invisible in behaviour: a tree simply comes out one
    level shallower or deeper than the operator's thresholds describe, and
    nothing else reports the discrepancy.
    """

    def test_a_unit_inside_every_limit_is_atomic(self) -> None:
        assessment = _policy().assess(
            _subtask(artifacts=_MAX_ARTIFACTS, criteria=_MAX_CRITERIA, satisfies=1)
        )

        assert assessment.verdict is AtomicityVerdict.ATOMIC
        assert assessment.condition is None
        assert not assessment.is_oversized

    def test_one_artifact_too_many_is_oversized(self) -> None:
        assessment = _policy().assess(_subtask(artifacts=_MAX_ARTIFACTS + 1))

        assert assessment.is_oversized
        assert assessment.condition == "expected_artifacts"
        assert assessment.observed == _MAX_ARTIFACTS + 1
        assert assessment.limit == _MAX_ARTIFACTS

    def test_one_criterion_too_many_is_oversized(self) -> None:
        assessment = _policy().assess(_subtask(criteria=_MAX_CRITERIA + 1))

        assert assessment.is_oversized
        assert assessment.condition == "acceptance_criteria"
        assert assessment.observed == _MAX_CRITERIA + 1

    def test_advancing_two_objective_criteria_is_two_units(self) -> None:
        assessment = _policy().assess(_subtask(satisfies=MAX_SATISFIED_CRITERIA + 1))

        assert assessment.is_oversized
        assert assessment.condition == "satisfies"

    def test_declaring_nothing_is_atomic(self) -> None:
        # The routing layer builds a bare, never-dispatched proxy definition,
        # and a size signal that called it oversized would split a subtask
        # that does not exist.
        assessment = _policy().assess(_subtask(artifacts=0, criteria=0, satisfies=0))

        assert assessment.verdict is AtomicityVerdict.ATOMIC


class TestTheReportedConditionIsStable:
    """Which rule is reported when several fire.

    A deeper-than-expected tree has two explanations, items genuinely too
    large or a threshold set too low, and only the named condition separates
    them. Reporting whichever count happened to overshoot furthest would make
    that name a function of the plan's prose rather than of the rule.
    """

    def test_the_first_rule_in_declaration_order_wins(self) -> None:
        assessment = _policy().assess(
            _subtask(
                artifacts=_MAX_ARTIFACTS + 5,
                criteria=_MAX_CRITERIA + 1,
                satisfies=MAX_SATISFIED_CRITERIA + 1,
            )
        )

        assert assessment.condition == "expected_artifacts"

    def test_a_later_rule_reports_when_the_earlier_ones_pass(self) -> None:
        assessment = _policy().assess(
            _subtask(
                artifacts=_MAX_ARTIFACTS,
                criteria=_MAX_CRITERIA,
                satisfies=MAX_SATISFIED_CRITERIA + 3,
            )
        )

        assert assessment.condition == "satisfies"
        assert assessment.observed == MAX_SATISFIED_CRITERIA + 3
