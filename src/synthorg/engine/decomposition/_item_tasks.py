# module-kind: code
"""The executable task one durable plan item becomes.

Split from :mod:`.plan_mapping`, which owns the projection between the whole
transient tree and the whole durable plan. This owns the one item: whether it
is work to do or the assembly of the work below it, and what that difference
changes about the task minted from it.

A container is not a second kind of thing to dispatch. It is an ordinary WORK
item whose description is an assembly brief, whose expected artifacts include
that subtree's own evidence, and whose stakes sit one rung above what it
assembles, so it runs through routing, waves and the review gate exactly as
every other item does.
"""

from dataclasses import dataclass

from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_tree import PlanTree
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import Stakes, TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.assembly import (
    AssemblyPaths,
    build_assembly_brief,
    escalated_stakes,
    subtree_assembly_paths,
)
from synthorg.engine.decomposition._artifacts import expected_artifact_from_spec
from synthorg.engine.decomposition._ids import subtask_uuid
from synthorg.engine.decomposition.plan_context import with_plan_context


@dataclass(frozen=True, slots=True)
class Assembly:
    """What a container item dispatches instead of the work below it.

    Attributes:
        brief: The assembly brief, naming its own children as the pieces.
        paths: Where this subtree's evidence goes, namespaced so siblings do
            not overwrite each other.
        stakes: One rung above the highest of what it assembles.
    """

    brief: str
    paths: AssemblyPaths
    stakes: Stakes


def assembly_of(item: PlanItem, *, tree: PlanTree) -> Assembly | None:
    """Describe *item* as an assembly, or answer ``None`` for a leaf.

    A container is not work: it is the assembly of the work below it, and
    dispatching it as work would do that work twice. What makes it one is
    having children, derived from the tree rather than declared, so the
    answer cannot drift from what the plan holds.

    Args:
        item: The item to describe.
        tree: The plan's containment view.

    Returns:
        The assembly, or ``None`` when nothing hangs off *item*.
    """
    children = tree.children(item.id)
    if not children:
        return None
    siblings = (
        tree.workstreams if item.parent_id is None else tree.children(item.parent_id)
    )
    paths = subtree_assembly_paths(
        str(item.title), index=[s.id for s in siblings].index(item.id)
    )
    return Assembly(
        brief=build_assembly_brief(
            objective_title=str(item.title),
            pieces=[str(child.title) for child in children],
            criteria=[str(criterion) for criterion in item.acceptance_criteria],
            paths=paths,
        ),
        paths=paths,
        stakes=escalated_stakes(children),
    )


def task_from_item(
    item: PlanItem,
    *,
    plan: Plan,
    objective: Task,
    parent_task_id: str,
    tree: PlanTree,
) -> Task:
    """Rebuild the child task for a plan item under *parent_task_id*.

    Uses the same deterministic id mapping as the decomposition service, so a
    re-dispatch of the same (possibly edited) plan targets stable task ids.

    The plan and item ids are stamped onto the task so the rollup can query a
    plan's tasks directly, rather than re-deriving the id mapping at every
    call site.

    The plan's settled and unsettled context rides on the description, which
    is where the answer to a parked question finally reaches an agent: the
    approval writes it onto ``plan.assumptions``, and nothing else on this
    path carries a plan-level fact down to the work.

    The routing context comes from the OBJECTIVE and the tree position comes
    from *parent_task_id*, which are the same task only for a workstream. A
    nested item hangs off its container's task, which is what makes
    ``tasks.parent_task_id`` a durable record of the tree rather than a flat
    fan of every item off the objective.

    Args:
        item: The durable item to rebuild.
        plan: The plan it belongs to, supplying the id and the settled context.
        objective: The task the whole plan decomposes, supplying the routing
            context every level inherits.
        parent_task_id: The task this one hangs off: its container's, or the
            objective's for a workstream.
        tree: The plan's containment view, which decides whether this item is
            work to do or the assembly of the work below it.

    Returns:
        A ``CREATED`` child :class:`Task` inheriting the objective's routing
        context (type, priority, project, delegation chain) and carrying the
        item's acceptance criteria and expected artifacts, so the task's
        fail-loud zero-artifact guard engages on the plan-review dispatch path.
    """
    assembly = assembly_of(item, tree=tree)
    body = item.description if assembly is None else NotBlankStr(assembly.brief)
    # Its own declarations PLUS the assembly's evidence: the first is what the
    # planner said this unit produces, the second is what shows the pieces run
    # together, and a probe can only credit a path it was given.
    artifacts = (
        item.expected_artifacts
        if assembly is None
        else (*item.expected_artifacts, *assembly.paths.declared)
    )
    return Task(
        id=subtask_uuid(item.id),
        title=item.title,
        description=NotBlankStr(
            with_plan_context(
                body,
                assumptions=plan.assumptions,
                open_questions=plan.open_questions,
            )
        ),
        type=objective.type,
        priority=objective.priority,
        project=objective.project,
        plan_id=plan.id,
        plan_item_id=subtask_uuid(item.id),
        created_by=objective.created_by,
        parent_task_id=parent_task_id,
        delegation_chain=objective.delegation_chain,
        dependencies=item.dependencies,
        acceptance_criteria=tuple(
            AcceptanceCriterion(description=c) for c in item.acceptance_criteria
        ),
        artifacts_expected=tuple(
            expected_artifact_from_spec(NotBlankStr(a)) for a in artifacts
        ),
        status=TaskStatus.CREATED,
        estimated_complexity=item.estimated_complexity,
        stakes=item.stakes if assembly is None else assembly.stakes,
    )


__all__ = ["Assembly", "assembly_of", "task_from_item"]
