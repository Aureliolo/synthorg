"""Task-board work-entry adapter.

Maps a human-filed :class:`TaskBoardFiling` onto a ``WorkItem`` with
``source=TASK_BOARD`` and drives the work pipeline spine. The board
controller spawns a detached background coroutine that calls
:meth:`TaskBoardEntryAdapter.submit`; the spine creates the task
inside its intake phase, so the adapter holds no task store and
performs no task-lifecycle reconciliation. The board's column moves
remain pure status walks of the spine-created task.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import Complexity, Priority, TaskType
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.pipeline.models import WorkItem, WorkSource

if TYPE_CHECKING:
    from synthorg.engine.pipeline.models import WorkPipelineResult
    from synthorg.engine.pipeline.protocol import WorkPipeline

_ORIGIN_ADAPTER_ID = "task-board-entry-adapter"


class TaskBoardFiling(BaseModel):
    """Validated board filing input crossing the controller -> adapter seam.

    Defined in the engine layer so the controller maps its HTTP DTO
    (``CreateTaskRequest``) onto a domain shape rather than handing
    ``api/dto`` types to the engine. Carries the correlation id so the
    controller can return it in the 202 envelope before the background
    pipeline run starts.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    title: NotBlankStr = Field(
        max_length=256,
        description="Short human-readable work title",
    )
    description: NotBlankStr = Field(
        max_length=4096,
        description="Detailed task description (becomes raw_intent)",
    )
    task_type: TaskType = Field(description="Classification of the work type")
    project: NotBlankStr = Field(description="Project the work belongs to")
    requested_by: NotBlankStr = Field(
        description="User id (or agent name) that filed the task",
    )
    priority: Priority = Field(
        default=Priority.MEDIUM,
        description="Work priority",
    )
    estimated_complexity: Complexity = Field(
        default=Complexity.MEDIUM,
        description="Complexity estimate (drives spine routing)",
    )
    correlation_id: NotBlankStr = Field(
        default_factory=lambda: str(uuid4()),
        description="End-to-end trace id; returned in the 202 envelope",
    )


class TaskBoardEntryAdapter:
    """Feeds board filings into the pipeline spine."""

    __slots__ = ("_work_pipeline",)

    def __init__(self, *, work_pipeline: WorkPipeline) -> None:
        """Initialise the adapter.

        Args:
            work_pipeline: The composed pipeline spine to drive.
        """
        self._work_pipeline = work_pipeline

    @property
    def source(self) -> WorkSource:
        """Provenance stamp for items this adapter produces."""
        return WorkSource.TASK_BOARD

    async def submit(self, filing: TaskBoardFiling) -> WorkPipelineResult:
        """Map ``filing`` onto a work item and drive the spine.

        Args:
            filing: The validated board filing to enter.

        Returns:
            The terminal :class:`WorkPipelineResult`.

        Raises:
            WorkPipelineError: Propagated unchanged from the spine.
        """
        work_item = WorkItem(
            origin_adapter_id=_ORIGIN_ADAPTER_ID,
            source=WorkSource.TASK_BOARD,
            title=filing.title,
            raw_intent=filing.description,
            project=filing.project,
            requested_by=filing.requested_by,
            priority=filing.priority,
            task_type=filing.task_type,
            estimated_complexity=filing.estimated_complexity,
            correlation_id=filing.correlation_id,
        )
        return await self._work_pipeline.run(work_item)
