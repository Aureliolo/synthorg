# module-kind: code
"""Re-plan a reviewed plan while its review still demands one.

The panel exists to catch a plan before the operator has to, and until this
loop the catching went nowhere: the verdict was synthesised, attached to the
durable plan, rendered, and the plan was parked for the human regardless. A
live run produced eighteen findings across four reviewers and the operator was
handed all eighteen, unaddressed, on a plan whose stated assumptions named
files that did not exist.

The loop is bounded by the panel's own ``max_revision_rounds``, because a
planner and a panel that disagree will disagree indefinitely and each round
costs a whole decomposition plus a whole panel. Reaching the cap is not a
failure: the plan is parked for the operator carrying whatever is still
outstanding, which is strictly what happened before, and the exhaustion is
logged so "the panel objected and nothing changed" is a readable state rather
than an inferred one.
"""

from collections.abc import Awaitable, Callable
from typing import NamedTuple

from synthorg.core.plan_review import PlanReviewOutcome
from synthorg.core.task import Task
from synthorg.engine.decomposition.models import DecompositionResult
from synthorg.engine.plan_review.revision_brief import (
    build_revision_brief,
    review_demands_revision,
)
from synthorg.observability import get_logger
from synthorg.observability.events.pipeline import (
    PIPELINE_PLAN_REVISION_EXHAUSTED,
    PIPELINE_PLAN_REVISION_SETTLED,
    PIPELINE_PLAN_REVISION_STARTED,
)

logger = get_logger(__name__)

PlanBuilder = Callable[[Task], Awaitable[DecompositionResult]]
PlanReviewer = Callable[[int, Task, DecompositionResult], Awaitable[PlanReviewOutcome]]

#: Phase name for the first panel run, before any revision.
PHASE_REVIEW_PANEL = "plan_review_panel"


def review_phase_name(round_index: int) -> str:
    """Name the phase for the panel run of revision round *round_index*.

    Each round gets its own phase so the operator reads how many panels ran
    rather than one entry whose duration silently covers several.

    Returns:
        The phase name for that round.
    """
    if round_index == 0:
        return PHASE_REVIEW_PANEL
    return f"{PHASE_REVIEW_PANEL}_revision_{round_index}"


class ReviewedPlan(NamedTuple):
    """The plan that survived review, and what reviewing it took.

    A tuple rather than a validating model, matching the spine's other
    internal result types: both halves arrive already validated by the
    collaborator that produced them, so re-checking them here would buy
    nothing and cost a second validation pass on every plan.

    Attributes:
        plan: The final decomposed plan, which is what gets parked.
        outcome: The review of that final plan, never of a superseded one.
        rounds_used: How many revision rounds ran. Zero means the first plan
            was accepted.
        settled: Whether the final review stopped demanding a revision. False
            means the cap was reached with findings outstanding, and the
            operator is being handed them.
    """

    plan: DecompositionResult
    outcome: PlanReviewOutcome
    rounds_used: int
    settled: bool


async def build_reviewed_plan(
    *,
    task: Task,
    build_plan: PlanBuilder,
    review_plan: PlanReviewer,
    max_rounds: int,
) -> ReviewedPlan:
    """Build a plan, review it, and re-plan while the review demands one.

    Args:
        task: The objective task being planned. Its description is the brief;
            each revision round re-plans from it with the findings appended.
        build_plan: Decomposes a task into a plan.
        review_plan: Reviews a plan, given the round index it belongs to so
            the caller can record each round as its own phase.
        max_rounds: The revision cap. Zero makes the panel advisory: it still
            reviews, and its findings still reach the durable plan and the
            operator, but nothing re-plans against them.

    Returns:
        The final plan, its own review, and what it took to get there.
    """
    plan = await build_plan(task)
    outcome = await review_plan(0, task, plan)

    rounds = 0
    while rounds < max_rounds and review_demands_revision(outcome.review):
        rounds += 1
        # Brief off the ORIGINAL description every round, never off the
        # previous round's briefed copy: findings describe the plan they were
        # raised against, so carrying a superseded round's forward asks the
        # planner to fix a plan that no longer exists, and the prompt grows
        # without bound.
        brief = build_revision_brief(review=outcome.review, note=None)
        revised = task.model_copy(
            update={"description": f"{task.description}\n\n{brief}"}
        )
        logger.info(
            PIPELINE_PLAN_REVISION_STARTED,
            task_id=str(task.id),
            round=rounds,
            max_rounds=max_rounds,
        )
        plan = await build_plan(revised)
        outcome = await review_plan(rounds, revised, plan)

    settled = not review_demands_revision(outcome.review)
    if rounds:
        logger.info(
            PIPELINE_PLAN_REVISION_SETTLED
            if settled
            else PIPELINE_PLAN_REVISION_EXHAUSTED,
            task_id=str(task.id),
            rounds_used=rounds,
            max_rounds=max_rounds,
        )
    return ReviewedPlan(
        plan=plan,
        outcome=outcome,
        rounds_used=rounds,
        settled=settled,
    )
