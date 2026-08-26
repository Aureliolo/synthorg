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

The level's WIDTH is corrected here too, and for the same reason: splitting
to atomicity is what pushes a level past its cap, so a level whose every unit
is now one agent's work is exactly the one likeliest to be over it. Either
condition alone earns a correction, because an over-cap level of atomic units
is the case a size-only gate cannot see at all, and the post-session guard
refuses that level outright: the session is spent and then the level fails
for a reason the planner was never given.

A level exactly AT the cap is the one case that stays silent. There is no
depth below it and no width beside it, so asking produces a plan the width cap
then refuses, which is what killed a twenty-one-session tree: the planner
widened to eleven against a limit of ten, exactly as instructed, and the run
failed on the result. Those units dispatch carrying their backstop reason
instead, which is what the depth backstop is for. Equality, not ``>=``: a
level already OVER the cap can still be saved by merging or dropping, so it is
exactly the level that needs to hear the cap named.

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
        The correction to hand back, or ``None`` when the level is within its
        width and every unit is already one agent's worth of work, or when
        widening is not available.
    """
    if policy is None:
        return None
    count = len(subtasks)
    if count == width_limit:
        # Exactly AT the cap, which is the one silent case: nowhere to put
        # anything, so asking produces a plan the width cap then refuses.
        return None
    over_cap = count > width_limit
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
    if not offenders and not over_cap:
        return None
    oversized = ""
    if offenders:
        named = ", ".join(
            f"{subtask.title!r} ({assessment.condition} is {assessment.observed}, "
            f"limit {assessment.limit})"
            for subtask, assessment in offenders[:_MAX_NAMED]
        )
        more = len(offenders) - _MAX_NAMED
        suffix = f", and {more} more" if more > 0 else ""
        oversized = (
            f"These units are still more than one agent's work: {named}"
            f"{suffix}. Split them into more units AT THIS LEVEL rather than "
            f"leaving them large, so that each unit produces at most "
            f"{policy.max_expected_artifacts} deliverable(s), defines at most "
            f"{policy.max_acceptance_criteria} acceptance criteria, and "
            f"advances at most one of the objective's success criteria. "
        )
    width = (
        f"This level submits {count} units, and the whole level must come to "
        f"at most {width_limit} units, so merge or drop what does not fit."
        if over_cap
        else f"The whole level must still come to at most {width_limit} units, "
        f"so merge or drop what does not fit rather than exceeding it."
    )
    return (
        f"This is the last level of planning available, so nothing here can "
        f"be broken down further later. {oversized}{width}"
    )


__all__ = ["describe_unsplittable"]
