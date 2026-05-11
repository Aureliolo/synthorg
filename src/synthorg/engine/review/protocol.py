"""Review pipeline stage protocol.

Defines the pluggable interface for review pipeline stages.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.core.task import Task
    from synthorg.engine.review.models import ReviewStageResult


# audit-keep 2026-05-10: 2 stage impls (ClientReviewStage, InternalReviewStage)
# + ReviewPipeline walker + 6 test files; pluggable pipeline stage.
@runtime_checkable
class ReviewStage(Protocol):
    """Protocol for a single stage in the review pipeline.

    Each stage evaluates a task and returns a verdict:

    * **PASS**: Task passes this stage; continue to next stage.
    * **FAIL**: Task fails; return to IN_PROGRESS for rework.
    * **SKIP**: Stage is not applicable; skip to next stage.

    The pipeline walks stages in order. On first FAIL, the
    pipeline short-circuits and the task transitions back to
    IN_PROGRESS with the stage name and reason in metadata.
    """

    @property
    def name(self) -> str:
        """Stage identifier used in pipeline results and metadata."""
        ...

    async def execute(
        self,
        task: Task,
    ) -> ReviewStageResult:
        """Execute this review stage on the given task.

        Args:
            task: The task to review (in IN_REVIEW status).

        Returns:
            Stage result with verdict, reason, and timing.
        """
        ...
