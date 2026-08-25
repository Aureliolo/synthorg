# module-kind: tests
"""A plan must advance the objective it decomposes.

``satisfies`` exists so success-criteria coverage can be checked, and nothing
checked it: the prompt states the contract, the schema leaves the field
optional, and its description invites omission per item. A planner reading every
item as pure support therefore produced a plan tagged with nothing, which parsed
cleanly and answered "which of the objective's criteria does this address?" with
silence. Observed live on a 42-criterion objective, on the same specification
where an earlier run of the same planner tagged every item.
"""

from typing import cast

import pytest
from pydantic import JsonValue

from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.llm_parse import args_to_decomposition_plan
from synthorg.engine.errors import DecompositionError

pytestmark = pytest.mark.unit

_CRITERIA: tuple[NotBlankStr, ...] = (
    NotBlankStr("R01 is satisfied"),
    NotBlankStr("R02 is satisfied"),
)


def _subtask(
    subtask_id: str, *, satisfies: list[str] | None = None
) -> dict[str, object]:
    """One well-formed subtask, claiming *satisfies* or nothing.

    Returns:
        The subtask arguments.
    """
    item: dict[str, object] = {
        "id": subtask_id,
        "title": f"Build {subtask_id}",
        "description": f"Implement the {subtask_id} component and its tests.",
        "dependencies": [],
        "stakes": "medium",
        "estimated_complexity": "medium",
        "required_skills": ["python"],
        "acceptance_criteria": [f"{subtask_id} verified"],
        "expected_artifacts": [f"src/{subtask_id}.py"],
    }
    if satisfies is not None:
        item["satisfies"] = satisfies
    return item


def _args(*subtasks: dict[str, object]) -> dict[str, JsonValue]:
    """Plan arguments carrying *subtasks*.

    Returns:
        The submit-plan arguments.
    """
    return cast("dict[str, JsonValue]", {"subtasks": list(subtasks)})


class TestAPlanMustAdvanceItsObjective:
    """The degenerate case is a plan claiming nothing at all."""

    def test_a_plan_claiming_nothing_is_refused(self) -> None:
        args = _args(_subtask("alpha"), _subtask("beta"))

        with pytest.raises(DecompositionError, match="advances none of the objective"):
            args_to_decomposition_plan(args, "task-1", (), _CRITERIA)

    def test_the_refusal_says_how_to_fix_it(self) -> None:
        # The message reaches the planning agent as a tool error and is the
        # only instruction it gets, so it has to name the field and the action.
        args = _args(_subtask("alpha"))

        with pytest.raises(DecompositionError) as caught:
            args_to_decomposition_plan(args, "task-1", (), _CRITERIA)

        detail = str(caught.value)
        assert "satisfies" in detail
        assert "copied verbatim" in detail
        assert str(len(_CRITERIA)) in detail

    def test_one_claiming_item_is_enough(self) -> None:
        # Plan level, not item level: the field's own semantics allow a genuine
        # pure-support item to claim nothing. What cannot hold is that every
        # item is pure support, because then nothing builds the objective.
        args = _args(
            _subtask("alpha", satisfies=["R01 is satisfied"]),
            _subtask("beta"),
        )

        plan = args_to_decomposition_plan(args, "task-1", (), _CRITERIA)

        assert len(plan.subtasks) == 2

    def test_a_plan_claiming_only_invented_criteria_is_refused(self) -> None:
        # A non-empty field is not coverage. `satisfies` carries criterion
        # TEXT, so a plan tagging every item with a sentence the objective
        # never states advances exactly as much as one tagging nothing, while
        # reading as covered on every surface that shows the field.
        args = _args(
            _subtask("alpha", satisfies=["invented criterion"]),
            _subtask("beta", satisfies=["another invention"]),
        )

        with pytest.raises(DecompositionError, match="advances none of the objective"):
            args_to_decomposition_plan(args, "task-1", (), _CRITERIA)

    def test_the_refusal_quotes_what_the_plan_claimed_instead(self) -> None:
        # Tagged-with-nothing and tagged-with-inventions are different faults
        # with different fixes, and only the second is invisible to the
        # planner: its items look tagged. The two lists side by side are what
        # let the next turn compare them rather than guess.
        args = _args(_subtask("alpha", satisfies=["invented criterion"]))

        with pytest.raises(DecompositionError) as caught:
            args_to_decomposition_plan(args, "task-1", (), _CRITERIA)

        assert "invented criterion" in str(caught.value)

    def test_partial_overlap_is_still_a_plan_worth_having(self) -> None:
        # FULL coverage is documented and deliberately not enforced: an item
        # may claim one real criterion alongside its own wording, and a rule
        # the planner keeps re-breaking in front of the retry ladder is how
        # the em-dash style rule once took 18 of 25 planning calls.
        args = _args(
            _subtask("alpha", satisfies=["R01 is satisfied", "some support work"]),
        )

        plan = args_to_decomposition_plan(args, "task-1", (), _CRITERIA)

        assert len(plan.subtasks) == 1

    def test_an_objective_with_no_criteria_has_no_coverage_to_claim(self) -> None:
        # Empty skips the check, matching how `available_roles` behaves in the
        # same parser: an objective declaring nothing cannot be under-covered.
        args = _args(_subtask("alpha"))

        plan = args_to_decomposition_plan(args, "task-1", (), ())

        assert len(plan.subtasks) == 1

    def test_the_check_is_off_by_default(self) -> None:
        # Every existing caller that passes no criteria keeps its behaviour, so
        # this cannot break a decomposition that was previously accepted.
        args = _args(_subtask("alpha"))

        plan = args_to_decomposition_plan(args, "task-1")

        assert len(plan.subtasks) == 1
