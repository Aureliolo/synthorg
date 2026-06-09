"""Client-delegated review stage."""

import time

from synthorg.client.models import (
    PoolConstraints,
    ReviewContext,
)
from synthorg.client.protocols import (
    ClientInterface,
    ClientPoolStrategy,
)
from synthorg.core.task import Task
from synthorg.engine.review.models import ReviewStageResult, ReviewVerdict
from synthorg.observability import get_logger

logger = get_logger(__name__)


class ClientReviewStage:
    """Delegates review to a client selected from a pool.

    Derives :class:`PoolConstraints` from the task, selects one
    client via the injected :class:`ClientPoolStrategy`, builds a
    :class:`ReviewContext` from the task, and invokes
    :meth:`ClientInterface.review_deliverable`. The returned
    :class:`ClientFeedback` maps onto
    :class:`ReviewStageResult`: ``accepted`` is ``PASS``,
    rejection is ``FAIL``. Scores and unmet criteria are included
    in the result metadata.
    """

    _NAME: str = "client"
    _NO_POOL_REASON: str = "no clients available in pool"
    _EMPTY_SELECTION_REASON: str = "pool strategy returned no clients for this task"

    def __init__(
        self,
        *,
        pool: tuple[ClientInterface, ...],
        strategy: ClientPoolStrategy,
    ) -> None:
        """Initialize the client review stage.

        Args:
            pool: Ordered tuple of candidate clients.
            strategy: Strategy used to select clients for each
                execution.
        """
        self._pool = pool
        self._strategy = strategy

    @property
    def name(self) -> str:
        """Stage identifier."""
        return self._NAME

    async def execute(self, task: Task) -> ReviewStageResult:
        """Select a client, delegate review, and map the feedback.

        Args:
            task: Task to review.

        Returns:
            Stage result with verdict, reason, and feedback
            metadata.
        """
        start_ns = time.perf_counter_ns()
        if not self._pool:
            duration = self._elapsed_ms(start_ns)
            return ReviewStageResult(
                stage_name=self._NAME,
                verdict=ReviewVerdict.SKIP,
                reason=self._NO_POOL_REASON,
                duration_ms=duration,
            )
        constraints = PoolConstraints(max_clients=1)
        selected = await self._strategy.select_clients(self._pool, constraints)
        if not selected:
            duration = self._elapsed_ms(start_ns)
            return ReviewStageResult(
                stage_name=self._NAME,
                verdict=ReviewVerdict.SKIP,
                reason=self._EMPTY_SELECTION_REASON,
                duration_ms=duration,
            )
        client = selected[0]
        context = self._build_context(task)
        feedback = await client.review_deliverable(context)
        duration = self._elapsed_ms(start_ns)
        verdict = ReviewVerdict.PASS if feedback.accepted else ReviewVerdict.FAIL
        return ReviewStageResult(
            stage_name=self._NAME,
            verdict=verdict,
            reason=feedback.reason,
            duration_ms=duration,
            metadata={
                "client_id": feedback.client_id,
                "feedback_id": feedback.feedback_id,
                "scores": feedback.scores or {},
                "unmet_criteria": list(feedback.unmet_criteria),
            },
        )

    @staticmethod
    def _elapsed_ms(start_ns: int) -> int:
        return max(0, (time.perf_counter_ns() - start_ns) // 1_000_000)

    @staticmethod
    def _build_context(task: Task) -> ReviewContext:
        criteria = tuple(c.description for c in task.acceptance_criteria)
        deliverable = task.description or task.title
        return ReviewContext(
            task_id=str(task.id),
            task_title=task.title,
            acceptance_criteria=criteria,
            deliverable_summary=deliverable,
        )
