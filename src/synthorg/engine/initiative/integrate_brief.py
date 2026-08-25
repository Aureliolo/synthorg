# module-kind: code
"""Bind the shared assembly brief to the initiative's root assembly.

The wording, the two evidence paths and the fencing live in
:mod:`synthorg.engine.assembly_brief`, which the container items of a
recursive plan bind to as well. This module supplies only what is the ROOT's
own: the objective it assembles, the workstreams it joins, and the objective's
success criteria.

Workstreams rather than every item, because a plan is a tree. Listing the whole
tree would hand the assembling agent a hundred titles for a job that joins
five, and each of those five was itself assembled by the container item that
owns it.
"""

from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.plan_tree import PlanTree
from synthorg.engine.assembly import (
    INTEGRATION_ARTIFACTS,
    INTEGRATION_REPORT_PATH,
    INTEGRATION_TEST_OUTPUT_PATH,
    ROOT_ASSEMBLY_PATHS,
    assembly_title,
    build_assembly_brief,
)

__all__ = [
    "INTEGRATION_ARTIFACTS",
    "INTEGRATION_REPORT_PATH",
    "INTEGRATION_TEST_OUTPUT_PATH",
    "build_integration_brief",
    "integration_title",
]


def integration_title(plan: Plan) -> str:
    """Return the board title for *plan*'s integration task.

    Returns:
        A title naming the objective being integrated.
    """
    return assembly_title(str(plan.objective_title))


def build_integration_brief(plan: Plan) -> str:
    """Compose the brief the root integration task runs against.

    Returns:
        The brief, naming the plan's workstreams as the pieces to assemble.
    """
    return build_assembly_brief(
        objective_title=str(plan.objective_title),
        pieces=[
            str(item.title)
            for item in PlanTree.of(plan.items).workstreams
            if item.kind is PlanItemKind.WORK
        ],
        criteria=[str(criterion) for criterion in plan.objective_criteria],
        paths=ROOT_ASSEMBLY_PATHS,
    )
