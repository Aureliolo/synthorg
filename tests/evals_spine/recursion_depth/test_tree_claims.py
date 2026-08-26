# module-kind: tests
"""Every claim in a tree resolves, asked before the tree costs anything.

A leaf session is minutes of real provider spend, and a cell is tens of them.
Asking "does every claim in this tree name a requirement" once, on the tree the
planner just produced, is what turns a broken map into a cell that cost its
planning sessions rather than one that spends its whole leaf budget and then
divides by an empty denominator.
"""

from uuid import UUID

import pytest

from evals.errors import RecursionDepthClaimUnresolvableError
from evals.recursion_depth.claims import RequirementId, criterion_for
from evals.recursion_depth.tree import claimed_requirements
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskStructure, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit

_KNOWN = (RequirementId("R01"), RequirementId("R02"))

_TITLES = {
    RequirementId("R01"): "The header row names the columns",
    RequirementId("R02"): "An integer column compares and sorts numerically",
}


def _criterion(identifier: RequirementId) -> NotBlankStr:
    """The criterion carrying *identifier*, as the root objective files it.

    Returns:
        The criterion text.
    """
    return NotBlankStr(criterion_for(identifier, _TITLES[identifier]))


def _subtask(label: str, *, satisfies: tuple[NotBlankStr, ...]) -> SubtaskDefinition:
    """Build one subtask claiming *satisfies*.

    Returns:
        The definition.
    """
    return SubtaskDefinition(
        id=NotBlankStr(sid(label)),
        title=NotBlankStr(f"Unit {label}"),
        description=NotBlankStr(f"Build {label}"),
        expected_artifacts=(NotBlankStr(f"src/{label}.py"),),
        acceptance_criteria=(NotBlankStr(f"{label} works"),),
        satisfies=satisfies,
    )


def _created(definition: SubtaskDefinition) -> Task:
    """Build the task one subtask definition becomes.

    Its id is the definition's, which is what the tree's own consistency rule
    requires and what makes a child level's parent resolvable.

    Returns:
        The task.
    """
    return Task(
        id=UUID(str(definition.id)),
        title=definition.title,
        description=definition.description,
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=NotBlankStr("proj-tree-claims"),
        created_by=NotBlankStr("operator"),
        status=TaskStatus.CREATED,
    )


def _level(
    parent: str,
    subtasks: tuple[SubtaskDefinition, ...],
    *,
    depth: int,
    children: tuple[DecompositionResult, ...] = (),
) -> DecompositionResult:
    """Build one planning level of a tree.

    Returns:
        The level.
    """
    return DecompositionResult(
        plan=DecompositionPlan(
            parent_task_id=NotBlankStr(parent),
            subtasks=subtasks,
            task_structure=TaskStructure.PARALLEL,
        ),
        created_tasks=tuple(_created(sub) for sub in subtasks),
        depth=depth,
        children=children,
    )


class TestEveryClaimResolves:
    def test_a_clean_tree_maps_every_unit_to_its_requirements(self) -> None:
        below = _level(
            sid("ingest"),
            (_subtask("header", satisfies=(_criterion(RequirementId("R01")),)),),
            depth=1,
        )
        tree = _level(
            str(as_uuid("root")),
            (
                _subtask(
                    "ingest",
                    satisfies=(
                        _criterion(RequirementId("R01")),
                        _criterion(RequirementId("R02")),
                    ),
                ),
            ),
            depth=0,
            children=(below,),
        )

        claimed = claimed_requirements(tree, known=_KNOWN)

        assert claimed[sid("ingest")] == ("R01", "R02")
        assert claimed[sid("header")] == ("R01",)

    def test_a_unit_claiming_nothing_maps_to_nothing(self) -> None:
        tree = _level(
            str(as_uuid("root")),
            (
                _subtask("choose", satisfies=()),
                _subtask("ingest", satisfies=(_criterion(RequirementId("R01")),)),
            ),
            depth=0,
        )

        assert claimed_requirements(tree, known=_KNOWN)[sid("choose")] == ()

    def test_an_unresolvable_claim_below_the_root_raises(self) -> None:
        """The level the shipped map used to lose, so this is the case.

        A root plan echoing the objective's criteria resolved perfectly and
        every level below it invented a fresh vocabulary, so a check that
        looked only at the root would have passed a whole broken sweep.
        """
        below = _level(
            sid("ingest"),
            (_subtask("header", satisfies=(NotBlankStr("First row is a header"),)),),
            depth=1,
        )
        tree = _level(
            str(as_uuid("root")),
            (_subtask("ingest", satisfies=(_criterion(RequirementId("R01")),)),),
            depth=0,
            children=(below,),
        )

        with pytest.raises(RecursionDepthClaimUnresolvableError) as caught:
            claimed_requirements(tree, known=_KNOWN)

        assert "Unit header" in str(caught.value)
