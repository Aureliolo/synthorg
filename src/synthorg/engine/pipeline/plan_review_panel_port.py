# module-kind: declarative
"""Port the work pipeline uses to run a stakeholder review of a built plan.

Dependency inversion: the pipeline depends on this port, not on the plan-review
engine that implements it. Between building a plan and parking it for human
approval, the spine hands the plan to the panel; the panel seats a bounded set
of leads, runs each as a review session, and returns a
:class:`~synthorg.core.plan_review.PlanReviewOutcome` the gate
attaches to the durable plan. An outcome always says something: either the
consolidated review, or why the plan carries none, so a plan that was never
reviewed is never indistinguishable from one nobody objected to.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.agent import AgentIdentity
from synthorg.core.plan_review import PlanReviewOutcome
from synthorg.core.task import Task
from synthorg.engine.decomposition.models import DecompositionResult


@runtime_checkable
class PlanReviewPanel(Protocol):
    """Runs a bounded stakeholder review of a built plan."""

    @property
    def max_revision_rounds(self) -> int:
        """How many re-plan rounds this panel's findings may drive.

        Every other bound on a review round is baked into the panel at
        construction and replaced by the reconciler when the operator writes
        one, so this rides with them rather than beside the spine: a second
        home for it would be a second answer to how long a disagreement runs.
        Zero makes the panel advisory.

        Returns:
            The maximum number of revision rounds, zero or more.
        """
        ...

    async def review(
        self,
        *,
        task: Task,
        plan: DecompositionResult,
        agents: tuple[AgentIdentity, ...],
        owner: AgentIdentity | None,
    ) -> PlanReviewOutcome:
        """Review *plan* with a bounded panel of leads.

        Args:
            task: The objective task the plan delivers.
            plan: The built plan the panel reviews.
            agents: The active roster the panel is seated from.
            owner: The plan's owner, excluded from the panel (no self-review).

        Returns:
            The consolidated review, or the reason the plan carries none.
        """
        ...
