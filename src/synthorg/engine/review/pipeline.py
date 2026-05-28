"""Review pipeline orchestrator."""

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.engine.review.models import (
    PipelineResult,
    ReviewStageResult,
    ReviewVerdict,
)
from synthorg.engine.review.protocol import ReviewStage
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.review_pipeline import (
    REVIEW_PIPELINE_COMPLETED,
    REVIEW_PIPELINE_STAGE_COMPLETED,
    REVIEW_PIPELINE_STARTED,
)

if TYPE_CHECKING:
    from synthorg.core.task import Task

logger = get_logger(__name__)


class ReviewPipeline:
    """Walks a chain of :class:`ReviewStage` implementations in order.

    On the first stage verdict of :attr:`ReviewVerdict.FAIL` the
    pipeline short-circuits and returns a
    :class:`PipelineResult` with ``final_verdict=FAIL``. A
    :attr:`ReviewVerdict.SKIP` verdict is treated as a no-op and
    execution continues to the next stage. If all stages pass (or
    skip), the final verdict is :attr:`ReviewVerdict.PASS`. The
    degenerate case of an empty stage tuple returns PASS.
    """

    def __init__(self, *, stages: tuple[ReviewStage, ...]) -> None:
        """Initialize the pipeline.

        Args:
            stages: Ordered tuple of stage implementations.
        """
        self._stages = stages

    @property
    def stages(self) -> tuple[ReviewStage, ...]:
        """Return the configured stages in execution order."""
        return self._stages

    @property
    def stage_names(self) -> tuple[str, ...]:
        """Return the ordered names of the configured stages."""
        return tuple(stage.name for stage in self._stages)

    async def run(self, task: Task) -> PipelineResult:
        """Execute every configured stage against the given task.

        Args:
            task: Task currently in ``IN_REVIEW`` status.

        Returns:
            Aggregated pipeline result including per-stage results,
            final verdict, and total duration.
        """
        logger.info(
            REVIEW_PIPELINE_STARTED,
            task_id=task.id,
            stage_count=len(self._stages),
            stages=list(self.stage_names),
        )
        start_ns = time.perf_counter_ns()
        stage_results: list[ReviewStageResult] = []
        final_verdict = ReviewVerdict.PASS
        for stage in self._stages:
            try:
                result = await stage.execute(task)
            except Exception as exc:
                log_exception_redacted(
                    logger,
                    REVIEW_PIPELINE_STAGE_COMPLETED,
                    exc,
                    task_id=task.id,
                    stage_name=getattr(stage, "name", type(stage).__name__),
                    verdict="error",
                )
                raise
            stage_results.append(result)
            logger.info(
                REVIEW_PIPELINE_STAGE_COMPLETED,
                task_id=task.id,
                stage_name=result.stage_name,
                verdict=result.verdict.value,
                duration_ms=result.duration_ms,
            )
            if result.verdict is ReviewVerdict.FAIL:
                final_verdict = ReviewVerdict.FAIL
                break

        total_ms = max(0, (time.perf_counter_ns() - start_ns) // 1_000_000)
        pipeline_result = PipelineResult(
            task_id=task.id,
            final_verdict=final_verdict,
            stage_results=tuple(stage_results),
            total_duration_ms=total_ms,
            reviewed_at=datetime.now(UTC),
        )
        logger.info(
            REVIEW_PIPELINE_COMPLETED,
            task_id=task.id,
            final_verdict=final_verdict.value,
            total_duration_ms=total_ms,
            stages_run=len(stage_results),
        )
        return pipeline_result
