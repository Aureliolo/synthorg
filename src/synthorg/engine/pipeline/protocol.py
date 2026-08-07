# module-kind: declarative
"""Work pipeline protocol.

The single coherent path every entry adapter feeds: a typed
:class:`WorkItem` in, a :class:`WorkPipelineResult` out.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.task import Task
from synthorg.engine.pipeline.models import (
    PipelineAttachments,
    WorkItem,
    WorkPipelineResult,
)
from synthorg.engine.pipeline.narrator_port import RunNarrator
from synthorg.engine.pipeline.plan_review_panel_port import PlanReviewPanel
from synthorg.engine.pipeline.plan_review_port import PlanReviewGate
from synthorg.engine.pipeline.refinement_port import WorkRefinementRouter


@runtime_checkable
class WorkPipeline(Protocol):
    """Composes intake, the solo-vs-team decision, and execution.

    Implementations are the single integration point every entry
    adapter feeds; they own no user-facing choice of solo vs team.
    """

    async def run(self, work_item: WorkItem) -> WorkPipelineResult:
        """Drive *work_item* through the full spine.

        Args:
            work_item: The typed entry envelope.

        Returns:
            The terminal :class:`WorkPipelineResult`.

        Raises:
            WorkPipelineError: On any phase failure (subclasses carry
                the precise RFC 9457 status).
        """
        ...

    async def intake_only(self, work_item: WorkItem) -> Task:
        """Run only intake and return the created task.

        Persists the task (stamping the human owner for the event stream)
        without running decomposition/execution, so a caller can surface
        the task id and background the rest via :meth:`continue_from_intake`.

        Args:
            work_item: The typed entry envelope.

        Returns:
            The task created by intake.

        Raises:
            WorkPipelineError: If intake rejects the request.
        """
        ...

    async def continue_from_intake(
        self, work_item: WorkItem, task: Task
    ) -> WorkPipelineResult:
        """Run the post-intake spine for an already-created task.

        Args:
            work_item: The typed entry envelope.
            task: The task created by a prior :meth:`intake_only` call.

        Returns:
            The terminal :class:`WorkPipelineResult`.

        Raises:
            WorkPipelineError: On any phase failure.
        """
        ...

    def attach_narrator(self, narrator: RunNarrator | None) -> None:
        """Attach (or clear) the post-run narrator (documentary mode).

        Late-bind seam: the narrator depends on services that wire only
        after persistence connects, so the startup hook attaches it to
        the already-built pipeline rather than passing it at construction.
        Passing ``None`` detaches it, which is how the reconciler takes the
        narrator down when a collaborator it captured is replaced.
        """
        ...

    def attach_refinement_router(self, router: WorkRefinementRouter | None) -> None:
        """Attach (or clear) the refinement router for under-specified team work.

        Late-bind seam: the router wraps the Chief-of-Staff proposer,
        which wires only after persistence and a provider are available,
        so the startup hook attaches it to the already-built pipeline.
        Absent, team-bound work with no definition of done is blocked by
        the coordinator's clarification gate instead of being refined.
        Passing ``None`` detaches it, which is how the reconciler takes the
        router down when the proposer it wraps is replaced.
        """
        ...

    def attach_plan_review_gate(self, gate: PlanReviewGate) -> None:
        """Attach the human plan-approval gate for splittable team work.

        Late-bind seam: the gate wraps the approval surface, which wires
        only after persistence is available, so the startup hook attaches
        it to the already-built pipeline. Absent, splittable team work
        dispatches straight to the coordinator (no human plan gate).
        """
        ...

    def attach_plan_review_panel(self, panel: PlanReviewPanel) -> None:
        """Attach the stakeholder plan-review panel for gated plans.

        Late-bind seam: the panel wraps a completion provider, which wires
        only after a provider is available, so the startup hook attaches it
        to the already-built pipeline. Absent, a gated plan is parked for
        human approval with no panel review.
        """
        ...

    @property
    def attachments(self) -> PipelineAttachments:
        """Report which late-bound collaborators are currently attached.

        The counterpart to the four ``attach_*`` seams. A subsystem that
        mutates the pipeline in place installs nothing else observable, so
        without this the reconciler would have to keep its own record of
        what it attached, which is exactly the second list the declarative
        model exists to remove.
        """
        ...
