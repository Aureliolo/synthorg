# module-kind: code
"""Ask the planner to widen a level when there is no depth left to split into.

While depth remains, an oversized unit is simply split, which is the measured
behaviour and the whole point of recursion. At the LAST permitted level there
is nowhere to split into, and what used to happen is that the unit was
dispatched whole behind a log line nobody reads: a live run left twenty-one
units carrying five to twelve objective criteria each against a limit of one.

So the question moves into the correction channel the planner already has. A
plan whose units cannot be split further is refused the same way a graph
violation is, and the session resubmits a WIDER level instead of a coarser
one: breadth spent where depth ran out. Both strategies converge on a
``DecompositionError`` raised while the submitted plan is parsed, so both
inherit this by reaching the same function.

Bounded by ``coordination.decomposition_max_retries`` like every other
correction. Only a plan that still cannot comply once those are spent reaches
the operator, as a reported condition on the items.
"""

from collections.abc import Sequence

from synthorg.core.plan_enums import PlanItemKind
from synthorg.engine.decomposition.atomicity import SubtaskAtomicityPolicy
from synthorg.engine.decomposition.models import SubtaskDefinition

#: How many offending units the correction names before it summarises. A
#: correction listing forty units is one the planner cannot act on, and the
#: rule it states applies to all of them anyway.
_MAX_NAMED: int = 5


def describe_unsplittable(
    subtasks: Sequence[SubtaskDefinition],
    *,
    policy: SubtaskAtomicityPolicy | None,
) -> str | None:
    """Name the units this level cannot split, and what to do instead.

    Args:
        subtasks: The plan as submitted.
        policy: The size signal this level is held to, or ``None`` when it is
            not the last level. ``DecompositionService`` is the single owner
            of that judgement and stamps the policy onto the context only
            where there is no depth left, so a level with room still splits
            rather than corrects: refusing there would trade the measured
            mechanism for an unmeasured one.

    Returns:
        The correction to hand back, or ``None`` when every unit is already
        one agent's worth of work.
    """
    if policy is None:
        return None
    offenders = [
        (subtask, assessment)
        for subtask in subtasks
        # A DECISION is a choice among its declared options rather than work
        # to divide, and the policy reads only counts, so one carrying several
        # criteria would read as oversized and be corrected into work nobody
        # asked for.
        if subtask.kind is PlanItemKind.WORK
        and (assessment := policy.assess(subtask)).is_oversized
    ]
    if not offenders:
        return None
    named = ", ".join(
        f"{subtask.title!r} ({assessment.condition} is {assessment.observed}, "
        f"limit {assessment.limit})"
        for subtask, assessment in offenders[:_MAX_NAMED]
    )
    more = len(offenders) - _MAX_NAMED
    suffix = f", and {more} more" if more > 0 else ""
    return (
        f"This is the last level of planning available, so nothing here can "
        f"be broken down further later. These units are still more than one "
        f"agent's work: {named}{suffix}. Split them into more units AT THIS "
        f"LEVEL rather than leaving them large, so that each unit produces at "
        f"most {policy.max_expected_artifacts} deliverable(s), defines at "
        f"most {policy.max_acceptance_criteria} acceptance criteria, and "
        f"advances at most one of the objective's success criteria."
    )


__all__ = ["describe_unsplittable"]
