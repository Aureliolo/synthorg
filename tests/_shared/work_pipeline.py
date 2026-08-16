"""Reusable :class:`WorkPipeline` test double.

Records calls and supports error injection on both the synchronous surface
(``intake_only`` / ``run``, which a caller awaits directly) and the
backgrounded spine (``continue_from_intake``), so a test can drive the
happy path, a synchronous-dispatch rejection, and a backgrounded failure
from one double.
"""

from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.pipeline.charter_authority_port import CharterAuthority
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    PipelineAttachments,
    RoutingVerdict,
    WorkItem,
    WorkPhaseResult,
    WorkPipelineResult,
)
from synthorg.engine.pipeline.narrator_port import RunNarrator
from synthorg.engine.pipeline.plan_review_panel_port import PlanReviewPanel
from synthorg.engine.pipeline.plan_review_port import PlanReviewGate
from synthorg.engine.pipeline.refinement_port import WorkRefinementRouter


def task_from_work_item(
    work_item: WorkItem, *, status: TaskStatus = TaskStatus.CREATED
) -> Task:
    """Build a minimal :class:`Task` mirroring an intake result.

    Returns:
        A task carrying the work item's title/description/project.
    """
    return Task(
        title=work_item.title,
        description=work_item.raw_intent,
        type=work_item.task_type,
        project=work_item.project,
        created_by=work_item.requested_by,
        status=status,
    )


def make_pipeline_result(work_item: WorkItem) -> WorkPipelineResult:
    """Build a terminal solo :class:`WorkPipelineResult` for *work_item*.

    Returns:
        A completed leaf/solo pipeline result.
    """
    return WorkPipelineResult(
        work_item=work_item,
        verdict=RoutingVerdict.LEAF,
        execution_path=ExecutionPath.SOLO,
        task_id="task-001",
        final_task_status=TaskStatus.COMPLETED,
        phases=(WorkPhaseResult(phase="intake", success=True, duration_seconds=0.01),),
        total_duration_seconds=0.01,
    )


class StubWorkPipeline:
    """In-memory :class:`WorkPipeline` double recording calls.

    ``intake_error`` raises from ``intake_only`` / ``run`` (the synchronous
    surface a caller awaits); ``continue_error`` raises from
    ``continue_from_intake`` (the backgrounded spine).
    """

    def __init__(
        self,
        *,
        intake_error: Exception | None = None,
        continue_error: Exception | None = None,
    ) -> None:
        self.calls: list[WorkItem] = []
        self.tasks: list[Task] = []
        self.continue_calls: list[tuple[WorkItem, Task]] = []
        self.intake_error = intake_error
        self.continue_error = continue_error
        self.narrator: RunNarrator | None = None
        self.refinement_router: WorkRefinementRouter | None = None
        self.plan_review_gate: PlanReviewGate | None = None
        self.plan_review_panel: PlanReviewPanel | None = None
        self.charter_authority: CharterAuthority | None = None

    async def run(self, work_item: WorkItem) -> WorkPipelineResult:
        self.calls.append(work_item)
        if self.intake_error is not None:
            raise self.intake_error
        return make_pipeline_result(work_item)

    async def intake_only(self, work_item: WorkItem) -> Task:
        self.calls.append(work_item)
        if self.intake_error is not None:
            raise self.intake_error
        task = task_from_work_item(work_item)
        # Retained so a test can stand a task repository on what intake
        # actually filed, rather than on a second, unrelated fixture that
        # can silently disagree with it.
        self.tasks.append(task)
        return task

    async def continue_from_intake(
        self, work_item: WorkItem, task: Task
    ) -> WorkPipelineResult:
        self.continue_calls.append((work_item, task))
        if self.continue_error is not None:
            raise self.continue_error
        return make_pipeline_result(work_item)

    def attach_charter_authority(self, authority: CharterAuthority | None) -> None:
        self.charter_authority = authority

    def attach_narrator(self, narrator: RunNarrator | None) -> None:
        self.narrator = narrator

    def attach_refinement_router(self, router: WorkRefinementRouter | None) -> None:
        self.refinement_router = router

    def attach_plan_review_gate(self, gate: PlanReviewGate) -> None:
        self.plan_review_gate = gate

    def attach_plan_review_panel(self, panel: PlanReviewPanel | None) -> None:
        self.plan_review_panel = panel

    @property
    def attachments(self) -> PipelineAttachments:
        """Report which collaborators the double has been handed.

        Returns:
            The attachment record the subsystem reconciler reads liveness from.
        """
        return PipelineAttachments(
            narrator=self.narrator is not None,
            refinement_router=self.refinement_router is not None,
            plan_review_gate=self.plan_review_gate is not None,
            plan_review_panel=self.plan_review_panel is not None,
            charter_authority=self.charter_authority is not None,
        )
