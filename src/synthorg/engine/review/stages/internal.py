"""Internal automated review stage."""

import time
from typing import TYPE_CHECKING

from synthorg.core.enums import TaskStatus
from synthorg.engine.review.models import ReviewStageResult, ReviewVerdict
from synthorg.observability import get_logger
from synthorg.observability.events.review_pipeline import REVIEW_STAGE_DECIDED

if TYPE_CHECKING:
    from synthorg.core.task import Task

logger = get_logger(__name__)


class InternalReviewStage:
    """Automated internal checks on a task in review.

    Performs a set of inexpensive sanity checks that do not
    require an external client opinion: the task must be in
    ``IN_REVIEW`` status, must have at least one acceptance
    criterion, and (optionally) must have every criterion marked
    as met. Any failed check produces a ``FAIL`` verdict with a
    specific reason.
    """

    _NAME: str = "internal"

    def __init__(self, *, require_all_criteria_met: bool = False) -> None:
        """Initialize the internal review stage.

        Args:
            require_all_criteria_met: When ``True``, fail if any
                acceptance criterion is marked as unmet. Default is
                ``False`` since criterion ``met`` flags are set by
                the reviewer, not by execution.
        """
        self._require_all_criteria_met = require_all_criteria_met

    @property
    def name(self) -> str:
        """Stage identifier."""
        return self._NAME

    async def execute(self, task: Task) -> ReviewStageResult:
        """Run the internal checks and return a result.

        Returns:
            A :class:`ReviewStageResult` carrying PASS or FAIL based
            on whether any internal check identified a failure.
        """
        start_ns = time.perf_counter_ns()
        failure = self._first_failure(task)
        duration_ms = max(0, (time.perf_counter_ns() - start_ns) // 1_000_000)
        if failure is not None:
            logger.info(
                REVIEW_STAGE_DECIDED,
                stage=self._NAME,
                task_id=task.id,
                verdict=ReviewVerdict.FAIL.value,
                reason=failure,
                duration_ms=duration_ms,
            )
            return ReviewStageResult(
                stage_name=self._NAME,
                verdict=ReviewVerdict.FAIL,
                reason=failure,
                duration_ms=duration_ms,
                metadata={"require_all_criteria_met": self._require_all_criteria_met},
            )
        logger.info(
            REVIEW_STAGE_DECIDED,
            stage=self._NAME,
            task_id=task.id,
            verdict=ReviewVerdict.PASS.value,
            criteria_count=len(task.acceptance_criteria),
            duration_ms=duration_ms,
        )
        return ReviewStageResult(
            stage_name=self._NAME,
            verdict=ReviewVerdict.PASS,
            duration_ms=duration_ms,
            metadata={"criteria_count": len(task.acceptance_criteria)},
        )

    def _first_failure(self, task: Task) -> str | None:
        if task.status is not TaskStatus.IN_REVIEW:
            return f"task status is {task.status.value}, expected 'in_review'"
        if not task.acceptance_criteria:
            return "task has no acceptance criteria"
        if self._require_all_criteria_met:
            unmet = [c.description for c in task.acceptance_criteria if not c.met]
            if unmet:
                return f"{len(unmet)} acceptance criteria not met"
        return None
