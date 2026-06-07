"""Integration tests for ReviewGateService.run_pipeline."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.enums import Priority, TaskStatus, TaskType
from synthorg.core.task import AcceptanceCriterion, Task
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
    engine.get_task = AsyncMock(return_value=task)
    return engine


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
        result = await service.run_pipeline(
            task_id=sid("task-1"),
            pipeline=pipeline,
            decided_by="bob",
            requested_by="bob",
        )
        assert result.final_verdict is ReviewVerdict.PASS
        engine.submit.assert_called_once()
        call = engine.submit.call_args[0][0]
        assert call.target_status is TaskStatus.COMPLETED

    async def test_pipeline_skip_all_completes_task(self) -> None:
        task = _task()
        engine = _mock_engine(task)
        service = ReviewGateService(task_engine=engine)
        pipeline = ReviewPipeline(
            stages=(_StaticStage(name="skippable", verdict=ReviewVerdict.SKIP),),
        )
        result = await service.run_pipeline(
            task_id=sid("task-1"),
            pipeline=pipeline,
            decided_by="bob",
            requested_by="bob",
        )
        assert result.final_verdict is ReviewVerdict.PASS
        call = engine.submit.call_args[0][0]
        assert call.target_status is TaskStatus.COMPLETED


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
        result = await service.run_pipeline(
            task_id=sid("task-1"),
            pipeline=pipeline,
            decided_by="bob",
            requested_by="bob",
        )
        assert result.final_verdict is ReviewVerdict.FAIL
        engine.submit.assert_called_once()
        call = engine.submit.call_args[0][0]
        assert call.target_status is TaskStatus.IN_PROGRESS
        assert "missing tests" in call.reason


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
                requested_by="bob",
            )
        engine.submit.assert_not_called()

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
                requested_by="bob",
            )
