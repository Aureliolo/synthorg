# module-kind: code
"""Asking the gates again after a park is released, and landing the answer.

The release hop alone would strand the task: nothing watches IN_REVIEW, and
the transition clears ``blocked_reason``, so the task also leaves the only
query the sweep runs. Asking the gates again is what makes the park heal
rather than merely move.

Which leaves the one verdict this path cannot honour. REWORK means "run this
again", and unlike the dispatch that originally ran the task there is no loop
here to run it: the gate has already written IN_PROGRESS, which nothing polls.
So it is landed on FAILED, naming the refusal, exactly as a dispatch does once
its rework rounds are spent and for the same reason.
"""

import asyncio
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.errors import TaskEngineError
from synthorg.engine.review.pipeline import ReviewPipeline
from synthorg.engine.review_gate import ReviewGateService
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.review_staffing import (
    REVIEW_STAFFING_REJUDGE_FAILED,
    REVIEW_STAFFING_REJUDGE_SENT_BACK,
    REVIEW_STAFFING_REJUDGED,
)

logger = get_logger(__name__)

#: Recorded when the re-judge sends the work back and this sweep has no loop
#: to answer it with. FAILED is re-runnable and watched; IN_PROGRESS, where
#: the gate left it, is neither.
REJUDGE_REWORK_REASON: Final[str] = (
    "Review sent the work back on re-judge, and no run was in flight to "
    "answer it; last reason: {reason}"
)


async def rejudge_released_task(
    task: Task,
    *,
    review_gate: ReviewGateService,
    review_pipeline: ReviewPipeline,
    task_engine: TaskEngine,
    actor: str,
) -> None:
    """Re-run the completion gates on a freshly released task.

    A failure leaves the task in review for a human, which is the same place
    an auto-review fault leaves it, so it is reported and not raised: the
    release itself already succeeded and re-parking the task would discard a
    correct transition.

    Args:
        task: The freshly released task.
        review_gate: Runs the staged pipeline.
        review_pipeline: The pipeline it runs.
        task_engine: Writes the FAILED landing for a rework verdict.
        actor: Who the re-judge is recorded as.

    Raises:
        asyncio.CancelledError: Propagated so a stopping scheduler is not
            recorded as a review failure.
    """
    try:
        run = await review_gate.run_pipeline(
            task_id=str(task.id),
            pipeline=review_pipeline,
            decided_by=actor,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- the release already succeeded, and raising
        # here would discard a correct transition; the task waits in review
        # for a human, where an auto-review fault leaves it too.
        reraise_critical(exc)
        logger.warning(
            REVIEW_STAFFING_REJUDGE_FAILED,
            task_id=str(task.id),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="released task waits for a human review decision",
        )
        return
    sent_back = run.rework_reason
    if sent_back is not None:
        await _fail_unrunnable_rework(
            task, sent_back, task_engine=task_engine, actor=actor
        )
        return
    logger.info(REVIEW_STAFFING_REJUDGED, task_id=str(task.id))


async def _fail_unrunnable_rework(
    task: Task,
    reason: str,
    *,
    task_engine: TaskEngine,
    actor: str,
) -> None:
    """Land a rework verdict this sweep has no loop to answer.

    Raises:
        asyncio.CancelledError: Propagated so a stopping scheduler is not
            recorded as a review failure.
    """
    try:
        await task_engine.transition_task(
            str(task.id),
            TaskStatus.FAILED,
            requested_by=actor,
            reason=REJUDGE_REWORK_REASON.format(reason=reason),
        )
    except asyncio.CancelledError:
        raise
    except TaskEngineError as exc:
        # Reported rather than raised for the same reason the release failure
        # is: one contended row must not stop the sweep. The task is left
        # IN_PROGRESS, which is named here because nothing else will name it.
        logger.error(
            REVIEW_STAFFING_REJUDGE_FAILED,
            task_id=str(task.id),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note=(
                "re-judge sent the work back and the FAILED landing did not"
                " commit; the task is IN_PROGRESS with nothing watching it"
            ),
        )
        return
    logger.warning(
        REVIEW_STAFFING_REJUDGE_SENT_BACK,
        task_id=str(task.id),
        reason=reason,
    )


__all__ = ["REJUDGE_REWORK_REASON", "rejudge_released_task"]
