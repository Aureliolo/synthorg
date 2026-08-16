"""Integration tests for ReviewGateService.run_pipeline."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.engine.errors import SelfReviewError
from synthorg.engine.review import (
    InternalReviewStage,
    ReviewPipeline,
    ReviewStageResult,
    ReviewVerdict,
)
from synthorg.engine.review_gate import ReviewGateService
from synthorg.engine.task_engine_models import TaskMutationResult
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.integration


def _task(*, status: TaskStatus = TaskStatus.IN_REVIEW) -> Task:
    return Task(
        id=as_uuid("task-1"),
        title="Test task",
        description=("Task description used for pipeline review tests."),
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="alice",
        assigned_to="alice",
        status=status,
        acceptance_criteria=(AcceptanceCriterion(description="Tests pass"),),
    )


def _mock_engine(task: Task) -> MagicMock:
    engine = MagicMock()
    engine.submit = AsyncMock(
        return_value=TaskMutationResult(request_id="req", success=True, version=1)
    )
    # The review gate commits through the strict ``transition_task`` seam.
    engine.transition_task = AsyncMock(return_value=(task, None))
    engine.get_task = AsyncMock(return_value=task)
    return engine


def _transition_call(engine: MagicMock) -> tuple[TaskStatus, str]:
    """Return ``(target_status, reason)`` from the recorded transition_task call.

    Returns:
        The target status (positional arg 1) and reason kwarg of the call.
    """
    call = engine.transition_task.call_args
    return call.args[1], call.kwargs["reason"]


class _StaticStage:
    def __init__(
        self,
        *,
        name: str,
        verdict: ReviewVerdict,
        reason: str | None = None,
    ) -> None:
        self._name = name
        self._verdict = verdict
        self._reason = reason

    @property
    def name(self) -> str:
        return self._name

    async def execute(self, task: Task) -> ReviewStageResult:
        del task
        return ReviewStageResult(
            stage_name=self._name,
            verdict=self._verdict,
            reason=self._reason,
        )


class TestRunPipelinePass:
    async def test_pipeline_pass_completes_task(self) -> None:
        task = _task()
        engine = _mock_engine(task)
        service = ReviewGateService(task_engine=engine)
        pipeline = ReviewPipeline(
            stages=(_StaticStage(name="stage-a", verdict=ReviewVerdict.PASS),),
        )
        run = await service.run_pipeline(
            task_id=sid("task-1"),
            pipeline=pipeline,
            decided_by="bob",
        )
        assert run.result.final_verdict is ReviewVerdict.PASS
        engine.transition_task.assert_awaited_once()
        target, _ = _transition_call(engine)
        assert target is TaskStatus.COMPLETED

    async def test_pipeline_skip_all_completes_task(self) -> None:
        task = _task()
        engine = _mock_engine(task)
        service = ReviewGateService(task_engine=engine)
        pipeline = ReviewPipeline(
            stages=(_StaticStage(name="skippable", verdict=ReviewVerdict.SKIP),),
        )
        run = await service.run_pipeline(
            task_id=sid("task-1"),
            pipeline=pipeline,
            decided_by="bob",
        )
        assert run.result.final_verdict is ReviewVerdict.PASS
        target, _ = _transition_call(engine)
        assert target is TaskStatus.COMPLETED


class TestRunPipelineFail:
    async def test_pipeline_fail_returns_in_progress(self) -> None:
        task = _task()
        engine = _mock_engine(task)
        service = ReviewGateService(task_engine=engine)
        pipeline = ReviewPipeline(
            stages=(
                _StaticStage(
                    name="failing",
                    verdict=ReviewVerdict.FAIL,
                    reason="missing tests",
                ),
            ),
        )
        run = await service.run_pipeline(
            task_id=sid("task-1"),
            pipeline=pipeline,
            decided_by="bob",
        )
        assert run.result.final_verdict is ReviewVerdict.FAIL
        engine.transition_task.assert_awaited_once()
        target, reason = _transition_call(engine)
        assert target is TaskStatus.IN_PROGRESS
        assert "missing tests" in reason


class TestRunPipelineFailedTask:
    """A FAILED task is decided on the failure contract, not a completion gate."""

    async def test_pipeline_pass_on_failed_task_acknowledges(self) -> None:
        task = _task(status=TaskStatus.FAILED)
        engine = _mock_engine(task)
        service = ReviewGateService(task_engine=engine)
        pipeline = ReviewPipeline(
            stages=(_StaticStage(name="stage-a", verdict=ReviewVerdict.PASS),),
        )
        run = await service.run_pipeline(
            task_id=sid("task-1"),
            pipeline=pipeline,
            decided_by="bob",
        )
        assert run.result.final_verdict is ReviewVerdict.PASS
        # Acknowledge: the task stays FAILED, so no completion transition runs
        # (the pipeline path must not launder a FAILED task into COMPLETED).
        engine.transition_task.assert_not_awaited()

    async def test_pipeline_fail_on_failed_task_retries_to_assigned(self) -> None:
        task = _task(status=TaskStatus.FAILED)
        engine = _mock_engine(task)
        service = ReviewGateService(task_engine=engine)
        pipeline = ReviewPipeline(
            stages=(
                _StaticStage(
                    name="failing",
                    verdict=ReviewVerdict.FAIL,
                    reason="still broken",
                ),
            ),
        )
        run = await service.run_pipeline(
            task_id=sid("task-1"),
            pipeline=pipeline,
            decided_by="bob",
        )
        assert run.result.final_verdict is ReviewVerdict.FAIL
        # Reject a failed run = rework via the sole valid exit from FAILED.
        engine.transition_task.assert_awaited_once()
        target, _ = _transition_call(engine)
        assert target is TaskStatus.ASSIGNED


class TestRunPipelineGuards:
    async def test_self_review_still_prevented(self) -> None:
        task = _task()
        engine = _mock_engine(task)
        service = ReviewGateService(task_engine=engine)
        pipeline = ReviewPipeline(
            stages=(InternalReviewStage(),),
        )
        with pytest.raises(SelfReviewError):
            await service.run_pipeline(
                task_id=sid("task-1"),
                pipeline=pipeline,
                decided_by="alice",  # same as assigned_to
            )
        engine.transition_task.assert_not_called()

    async def test_missing_task_raises(self) -> None:
        engine = _mock_engine(_task())
        engine.get_task = AsyncMock(return_value=None)
        service = ReviewGateService(task_engine=engine)
        pipeline = ReviewPipeline(stages=(InternalReviewStage(),))
        with pytest.raises(Exception, match="not found"):
            await service.run_pipeline(
                task_id="missing",
                pipeline=pipeline,
                decided_by="bob",
            )
