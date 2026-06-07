"""Unit tests for ReviewGateService vision-gate routing.

Fakes the ``VisionVerifierGate`` so the service-level routing contract
is exercised in isolation: a BLOCK verdict overrides the pipeline's
COMPLETED target and routes to IN_PROGRESS; a missing vision_input SKIPs
(the gate is conditional on a GUI deliverable); no gate passes through.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import AutonomyLevel, Priority, TaskStatus, TaskType
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.engine.review import ReviewPipeline, ReviewStageResult, ReviewVerdict
from synthorg.engine.review_gate import ReviewGateService
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskMutationResult
from synthorg.security.visionverify.models import (
    VisionFinding,
    VisionFindingCategory,
    VisionGateResult,
    VisionReviewInput,
    VisionScreenshotRef,
    VisionSeverity,
    VisionVerdict,
    VisionVerificationReport,
)
from synthorg.security.visionverify.protocol import VisionVerifierGate
from tests._shared import as_uuid, mock_of

pytestmark = pytest.mark.unit


def _task() -> Task:
    return Task(
        id=as_uuid("task-v-1"),
        title="GUI app",
        description="Build a counter GUI app.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-v",
        created_by="alice",
        assigned_to="agent-frontend",
        status=TaskStatus.IN_REVIEW,
        acceptance_criteria=(
            AcceptanceCriterion(description="Window background is blue."),
        ),
    )


def _mock_task_engine(task: Task) -> Any:
    return mock_of[TaskEngine](
        submit=AsyncMock(
            return_value=TaskMutationResult(
                request_id="req",
                success=True,
                version=1,
            ),
        ),
        get_task=AsyncMock(return_value=task),
    )


def _vision_input() -> VisionReviewInput:
    return VisionReviewInput(
        task_id="task-v-1",
        execution_id="exec-v-1",
        brief="a blue window",
        acceptance_criteria=("window background is blue",),
        screenshots=(VisionScreenshotRef(workspace_path="shot.png", sha256="d" * 64),),
        generator_agent_id="agent-frontend",
        evaluator_agent_id="vision",
        autonomy=AutonomyLevel.SUPERVISED,
    )


def _block_result() -> VisionGateResult:
    report = VisionVerificationReport(
        task_id="task-v-1",
        execution_id="exec-v-1",
        findings=(
            VisionFinding(
                category=VisionFindingCategory.REQUIREMENTS_MISMATCH,
                severity=VisionSeverity.HIGH,
                description="Window is red, brief requires blue",
                evidence=("measured red, expected blue",),
            ),
        ),
        summary="HIGH: window background is red, brief requires blue.",
        verifier_kind="heuristic",
        confidence=1.0,
        generator_agent_id="agent-frontend",
        evaluator_agent_id="vision",
    )
    return VisionGateResult(
        verdict=VisionVerdict.BLOCK,
        report=report,
        elapsed_seconds=0.01,
    )


def _gate(result: VisionGateResult) -> Any:
    return mock_of[VisionVerifierGate](
        evaluate=AsyncMock(spec=VisionVerifierGate.evaluate, return_value=result),
    )


def _pipeline() -> ReviewPipeline:
    class _PassingStage:
        name = "structural-pass"

        async def execute(self, task: Task) -> ReviewStageResult:
            del task
            return ReviewStageResult(
                stage_name=self.name,
                verdict=ReviewVerdict.PASS,
                reason="scripted pass",
            )

    return ReviewPipeline(stages=(_PassingStage(),))


async def test_block_routes_to_in_progress() -> None:
    task = _task()
    task_engine = _mock_task_engine(task)
    service = ReviewGateService(
        task_engine=task_engine,
        vision_gate=_gate(_block_result()),
    )
    await service.run_pipeline(
        task_id="task-v-1",
        pipeline=_pipeline(),
        decided_by="bob",
        requested_by="bob",
        vision_input=_vision_input(),
    )
    call = task_engine.submit.call_args[0][0]
    assert call.target_status is TaskStatus.IN_PROGRESS
    assert "Vision review blocked" in call.reason


async def test_missing_vision_input_skips() -> None:
    task = _task()
    task_engine = _mock_task_engine(task)
    service = ReviewGateService(
        task_engine=task_engine,
        vision_gate=_gate(_block_result()),
    )
    await service.run_pipeline(
        task_id="task-v-1",
        pipeline=_pipeline(),
        decided_by="bob",
        requested_by="bob",
        # no vision_input -> non-GUI deliverable -> gate SKIPs
    )
    call = task_engine.submit.call_args[0][0]
    assert call.target_status is TaskStatus.COMPLETED


async def test_no_gate_passes_through() -> None:
    task = _task()
    task_engine = _mock_task_engine(task)
    service = ReviewGateService(task_engine=task_engine)  # no vision gate
    await service.run_pipeline(
        task_id="task-v-1",
        pipeline=_pipeline(),
        decided_by="bob",
        requested_by="bob",
        vision_input=_vision_input(),
    )
    call = task_engine.submit.call_args[0][0]
    assert call.target_status is TaskStatus.COMPLETED


async def test_set_vision_gate_seam() -> None:
    task = _task()
    task_engine = _mock_task_engine(task)
    service = ReviewGateService(task_engine=task_engine)
    service.set_vision_gate(_gate(_block_result()))
    await service.run_pipeline(
        task_id="task-v-1",
        pipeline=_pipeline(),
        decided_by="bob",
        requested_by="bob",
        vision_input=_vision_input(),
    )
    call = task_engine.submit.call_args[0][0]
    assert call.target_status is TaskStatus.IN_PROGRESS
