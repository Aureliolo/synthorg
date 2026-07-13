# module-kind: declarative
"""Port the work pipeline uses to run a stakeholder review of a built plan.

Dependency inversion: the pipeline depends on this port, not on the plan-review
engine that implements it. Between building a plan and parking it for human
approval, the spine hands the plan to the panel; the panel seats a bounded set
of leads, runs each as a review session, and returns a consolidated
:class:`~synthorg.core.plan_review.PlanReview` the gate attaches to the durable
plan. A ``None`` return means the panel could not run (no eligible reviewer),
in which case the plan is parked for approval without a panel review.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.agent import AgentIdentity
from synthorg.core.plan_review import PlanReview
from synthorg.core.task import Task
from synthorg.engine.decomposition.models import DecompositionResult


@runtime_checkable
class PlanReviewPanel(Protocol):
    """Runs a bounded stakeholder review of a built plan."""

    async def review(
        self,
        *,
        task: Task,
        plan: DecompositionResult,
        agents: tuple[AgentIdentity, ...],
        owner: AgentIdentity | None,
    ) -> PlanReview | None:
        """Review *plan* with a bounded panel of leads.

        Args:
            task: The objective task the plan delivers.
            plan: The built plan the panel reviews.
            agents: The active roster the panel is seated from.
            owner: The plan's owner, excluded from the panel (no self-review).

        Returns:
            The consolidated :class:`PlanReview`, or ``None`` when no eligible
            reviewer could be seated.
        """
        ...
