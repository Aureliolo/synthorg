# module-kind: declarative
"""Port the work pipeline uses to gate a decomposed plan on human approval.

Dependency inversion: the pipeline depends on this port, not on the
approval / conversational machinery that implements it, so the engine
never imports the meta or persistence layers. When splittable team work
is decomposed into a plan and the org runs with a plan-approval gate, the
spine hands the plan to the gate instead of dispatching the team; the gate
persists the plan and parks a plan-approval item, returning a
:class:`~synthorg.engine.pipeline.models.PlanReviewHandoff` the caller can
surface. Nothing builds until the plan is approved, at which point the
exact approved plan is dispatched (no re-decomposition).
"""

from typing import Protocol, runtime_checkable
from uuid import UUID

from synthorg.core.plan_review import PlanReviewOutcome
from synthorg.core.task import Task
from synthorg.engine.decomposition.models import DecompositionResult
from synthorg.engine.pipeline.models import PlanReviewHandoff, WorkItem


@runtime_checkable
class PlanReviewGate(Protocol):
    """Parks a decomposed plan for human approval before a team builds.

    The plan is first-class from greenlight: :meth:`open_plan` persists a
    PLANNING shell before decomposition runs, :meth:`request_plan_approval`
    fills it and parks the approval on success, and :meth:`fail_plan` marks it
    FAILED on a decomposition failure, so a failed run always leaves a visible
    plan rather than a silent orphan.
    """

    async def open_plan(self, *, work_item: WorkItem, task: Task) -> UUID:
        """Persist a PLANNING plan shell before decomposition runs.

        Args:
            work_item: The originating entry envelope.
            task: The persisted parent (objective) task being planned.

        Returns:
            The id of the persisted PLANNING shell, threaded back into
            :meth:`request_plan_approval` / :meth:`fail_plan`.
        """
        ...

    async def request_plan_approval(
        self,
        *,
        plan_id: UUID,
        work_item: WorkItem,
        task: Task,
        plan: DecompositionResult,
        review: PlanReviewOutcome,
    ) -> PlanReviewHandoff:
        """Fill the shell with *plan* and park it for human approval.

        Args:
            plan_id: The PLANNING shell returned by :meth:`open_plan` to fill.
            work_item: The originating entry envelope.
            task: The persisted parent task that was decomposed.
            plan: The decomposed subtask tree awaiting approval; the gate
                persists it so the exact approved plan is what later builds.
            review: What the review attempt produced: a consolidated review,
                or the reason the plan carries none. Required, because a plan
                reaching the approval gate with neither is the blank review
                section this outcome type exists to make unrepresentable.

        Returns:
            A :class:`PlanReviewHandoff` the caller surfaces so the human
            can approve the plan.
        """
        ...

    async def fail_plan(self, *, plan_id: UUID, reason: str) -> None:
        """Mark the persisted plan FAILED on any plan-review failure.

        Called for every failure in the plan-review sequence, not only an
        unfilled ``PLANNING`` shell: a decomposition failure, but also a panel
        failure or an approval-store write failure on an already-filled
        ``PENDING_REVIEW`` plan (which is then FAILED with its items intact). A
        plan already FAILED, or a missing shell, is a no-op.

        Args:
            plan_id: The plan (a ``PLANNING`` shell or an already-filled
                ``PENDING_REVIEW`` plan) returned by :meth:`open_plan`.
            reason: A scrubbed description of why the plan failed, surfaced on
                the durable plan so the failure is visible in Plan Review.
        """
        ...
