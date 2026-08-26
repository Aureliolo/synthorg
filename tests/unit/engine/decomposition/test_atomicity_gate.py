"""What happens when there is no depth left to split an oversized unit into.

Before this, the unit was dispatched whole behind a log line: a live run left
twenty-one units carrying five to twelve objective criteria each against a
limit of one. Now the level is handed back to be widened instead.
"""

import pytest

from synthorg.core.plan import PlanOption
from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition._atomicity_gate import describe_unsplittable
from synthorg.engine.decomposition.atomicity import (
    DEPTH_BACKSTOP,
    AtomicityAssessment,
    AtomicityVerdict,
    SubtaskAtomicityPolicy,
    unsplit_reason,
)
from synthorg.engine.decomposition.models import SubtaskDefinition
from tests._shared import sid

pytestmark = pytest.mark.unit

_POLICY = SubtaskAtomicityPolicy(max_expected_artifacts=1, max_acceptance_criteria=2)

#: Comfortably above every level these cases submit, so the width cap is not
#: what any of them is measuring. The two that ARE about it say so.
_ROOM_TO_WIDEN = 20


def _subtask(
    label: str, *, artifacts: int = 1, criteria: int = 1, satisfies: int = 1
) -> SubtaskDefinition:
    return SubtaskDefinition(
        id=NotBlankStr(sid(label)),
        title=NotBlankStr(f"Unit {label}"),
        description=NotBlankStr(f"Build {label}"),
        expected_artifacts=tuple(
            NotBlankStr(f"src/{label}_{index}.py") for index in range(artifacts)
        ),
        acceptance_criteria=tuple(
            NotBlankStr(f"{label} works {index}") for index in range(criteria)
        ),
        satisfies=tuple(NotBlankStr(f"R{index:02d}") for index in range(satisfies)),
    )


def _decision(label: str) -> SubtaskDefinition:
    return SubtaskDefinition(
        id=NotBlankStr(sid(label)),
        title=NotBlankStr(f"Decision {label}"),
        description=NotBlankStr(f"Choose for {label}"),
        acceptance_criteria=tuple(
            NotBlankStr(f"the choice {index} is recorded") for index in range(5)
        ),
        kind=PlanItemKind.DECISION,
        options=(
            PlanOption(
                id=NotBlankStr("a"),
                title=NotBlankStr("A"),
                summary=NotBlankStr("One way"),
                recommended=True,
            ),
            PlanOption(
                id=NotBlankStr("b"),
                title=NotBlankStr("B"),
                summary=NotBlankStr("Another way"),
            ),
        ),
    )


class TestDescribeUnsplittable:
    def test_says_nothing_while_depth_remains(self) -> None:
        # The policy is absent exactly when a child level is still available,
        # and an oversized unit is split there rather than corrected.
        assert (
            describe_unsplittable(
                (_subtask("a", artifacts=9),),
                policy=None,
                width_limit=_ROOM_TO_WIDEN,
            )
            is None
        )

    def test_says_nothing_when_every_unit_is_atomic(self) -> None:
        assert (
            describe_unsplittable(
                (_subtask("a"),), policy=_POLICY, width_limit=_ROOM_TO_WIDEN
            )
            is None
        )

    def test_names_the_offending_unit_and_its_condition(self) -> None:
        detail = describe_unsplittable(
            (_subtask("a", artifacts=4),), policy=_POLICY, width_limit=_ROOM_TO_WIDEN
        )
        assert detail is not None
        assert "Unit a" in detail
        assert "expected_artifacts is 4" in detail
        assert "limit 1" in detail

    def test_asks_for_breadth_rather_than_depth(self) -> None:
        detail = describe_unsplittable(
            (_subtask("a", artifacts=4),), policy=_POLICY, width_limit=_ROOM_TO_WIDEN
        )
        assert detail is not None
        assert "AT THIS LEVEL" in detail

    def test_fires_on_the_objective_criteria_rule(self) -> None:
        detail = describe_unsplittable(
            (_subtask("a", satisfies=3),), policy=_POLICY, width_limit=_ROOM_TO_WIDEN
        )
        assert detail is not None
        assert "satisfies is 3" in detail

    def test_ignores_a_decision_item(self) -> None:
        # A decision is a choice among its options, not work to divide, and it
        # reads as oversized on criterion count alone.
        assert (
            describe_unsplittable(
                (_decision("stack"),), policy=_POLICY, width_limit=_ROOM_TO_WIDEN
            )
            is None
        )

    def test_states_the_ceiling_the_whole_level_must_stay_under(self) -> None:
        # The correction is the ONLY place the planner is told this number.
        # Without it a live run was ordered to widen, widened to eleven
        # against a limit of ten, and the run was failed on the result.
        detail = describe_unsplittable(
            (_subtask("a", artifacts=4),), policy=_POLICY, width_limit=6
        )
        assert detail is not None
        assert "at most 6 units" in detail

    def test_says_nothing_when_the_level_is_already_at_its_width_cap(self) -> None:
        # No depth below and no width beside: there is nowhere for the planner
        # to put anything, so asking produces a plan the width cap then
        # refuses. The units dispatch carrying their backstop reason instead.
        assert (
            describe_unsplittable(
                tuple(_subtask(f"n{index}", artifacts=4) for index in range(6)),
                policy=_POLICY,
                width_limit=6,
            )
            is None
        )

    def test_a_level_already_over_the_cap_is_still_corrected(self) -> None:
        # Distinct from the level AT the cap above, and the distinction is the
        # whole point: this plan is refused outright by the post-session width
        # guard, so staying silent spends the session and then fails the level
        # with the planner never told what was wrong. The correction names the
        # cap and says to merge or drop, which is the one thing that can still
        # save it.
        detail = describe_unsplittable(
            tuple(_subtask(f"n{index}", artifacts=4) for index in range(8)),
            policy=_POLICY,
            width_limit=6,
        )

        assert detail is not None
        assert "at most 6 units" in detail
        assert "merge or drop" in detail

    def test_summarises_past_the_naming_cap(self) -> None:
        detail = describe_unsplittable(
            tuple(_subtask(f"n{i}", artifacts=4) for i in range(9)),
            policy=_POLICY,
            width_limit=_ROOM_TO_WIDEN,
        )
        assert detail is not None
        assert "and 4 more" in detail


class TestUnsplitReason:
    def test_names_the_rule_the_numbers_and_the_backstop(self) -> None:
        reason = unsplit_reason(
            AtomicityAssessment(
                verdict=AtomicityVerdict.OVERSIZED,
                condition="satisfies",
                observed=7,
                limit=1,
            ),
            backstop=DEPTH_BACKSTOP,
        )
        assert "satisfies is 7" in reason
        assert "limit of 1" in reason
        assert DEPTH_BACKSTOP in reason

    def test_refuses_to_explain_an_atomic_verdict(self) -> None:
        with pytest.raises(ValueError, match="no unsplit reason"):
            unsplit_reason(
                AtomicityAssessment(verdict=AtomicityVerdict.ATOMIC),
                backstop=DEPTH_BACKSTOP,
            )
