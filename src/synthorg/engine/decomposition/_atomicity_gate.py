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
correction. A plan that still cannot comply once those are spent raises
``DecompositionUnsplittableError``, typed apart from every other decomposition
failure precisely so the level that asked for this one can answer it: that
level's own plan is valid, so it files ``PLANNER_DECLINED`` on the unit and
dispatches it whole rather than discarding a tree it already paid for.
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
    width_limit: int,
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
        width_limit: How many units this level may hold. Load-bearing twice
            over, and a live run died for the lack of it: the correction is
            the only place the planner is told, and a level already at the
            limit cannot be asked to widen at all.

    Returns:
        The correction to hand back, or ``None`` when every unit is already
        one agent's worth of work, or when widening is not available.
    """
    if policy is None:
        return None
    if len(subtasks) == width_limit:
        # Nowhere left to go: no depth below, no width beside. Asking anyway
        # is asking for a plan the width cap then refuses, which is what
        # killed a twenty-one-session tree: the planner widened to eleven
        # against a limit of ten, exactly as instructed, and the run failed
        # on the result. The units dispatch carrying their backstop reason
        # instead, which is what the depth backstop is for.
        #
        # EQUALITY, not ``>=``. A level already OVER the cap is a different
        # case: the post-session guard refuses it outright, so staying silent
        # here spends the session and then fails the level anyway, with the
        # planner never told what was wrong. The correction below names the
        # cap and says to merge or drop, which is the one thing that can
        # still save it, so it is exactly the level that needs to hear it.
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
        f"advances at most one of the objective's success criteria. "
        f"The whole level must still come to at most {width_limit} units, "
        f"so merge or drop what does not fit rather than exceeding it."
    )


__all__ = ["describe_unsplittable"]
