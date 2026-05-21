"""Acceptance test for the virtual-desktop vision verifier.

Validates the full acceptance criterion under the simulation harness's
service-level contract: an org builds a GUI app, an agent operates it on
the virtual desktop and captures a screenshot, and the vision verifier
flags a DELIBERATE brief-mismatch before the deliverable is marked done.

Concretely: a deliberate-mismatch screenshot (red window, brief requires
blue) drives the deterministic ``HeuristicVisionVerifier`` to a BLOCK
verdict; the ``ReviewGateService`` consumes it and routes the task
IN_REVIEW -> IN_PROGRESS (rework) instead of COMPLETED. A control run
with a matching (blue) screenshot completes.

Mirrors ``tests/integration/security/redteam/test_review_gate_red_team.py``:
the red-team gate's planted-defect acceptance, applied to the UI cousin.
"""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from synthorg.core.enums import AutonomyLevel, Priority, TaskStatus, TaskType
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.engine.review import ReviewPipeline, ReviewStageResult, ReviewVerdict
from synthorg.engine.review_gate import ReviewGateService
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskMutationResult
from synthorg.security.visionverify.gate import VisionVerifierGateService
from synthorg.security.visionverify.models import (
    VisionReviewInput,
    VisionScreenshotRef,
    VisualExpectation,
    VisualExpectationKind,
)
from synthorg.security.visionverify.verifiers import HeuristicVisionVerifier
from tests._shared import FakeClock, mock_of

pytestmark = pytest.mark.integration

# Brief: a window with a blue background. The org's deliverable
# deliberately mismatches by rendering a red window.
_EXPECTED_BLUE = (0, 0, 255)
_DELIBERATE_RED = (220, 20, 20)
_MATCHING_BLUE = (10, 10, 235)
_SCREENSHOT_REL = ".synthorg/desktop/screenshots/counter-app.png"
_SHA = "e" * 64


def _task() -> Task:
    return Task(
        id="task-gui-1",
        title="Counter GUI app",
        description="Build a counter app with a blue window background.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-gui",
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


class _PassingStage:
    """Structural stage that passes so the pipeline goes COMPLETED."""

    name = "structural-pass"

    async def execute(self, task: Task) -> ReviewStageResult:
        del task
        return ReviewStageResult(
            stage_name=self.name,
            verdict=ReviewVerdict.PASS,
            reason="Structural checks passed.",
        )


def _write_app_screenshot(workspace: Path, rgb: tuple[int, int, int]) -> None:
    """Emulate the DesktopTool capturing the running app's window."""
    path = workspace / _SCREENSHOT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (200, 150), rgb).save(path)


def _vision_input() -> VisionReviewInput:
    return VisionReviewInput(
        task_id="task-gui-1",
        execution_id="exec-gui-1",
        brief="A desktop counter app whose window background is blue.",
        acceptance_criteria=("Window background is blue.",),
        screenshots=(VisionScreenshotRef(workspace_path=_SCREENSHOT_REL, sha256=_SHA),),
        expectations=(
            VisualExpectation(
                kind=VisualExpectationKind.DOMINANT_COLOUR,
                description="window background should be blue",
                expected_rgb=_EXPECTED_BLUE,
                tolerance=0.15,
            ),
        ),
        generator_agent_id="agent-frontend",
        evaluator_agent_id="vision-verifier",
        autonomy=AutonomyLevel.SUPERVISED,
    )


def _build_gate_service(workspace: Path) -> tuple[ReviewGateService, Any]:
    task_engine = _mock_task_engine(_task())
    verifier = HeuristicVisionVerifier(workspace=workspace)
    vision_gate = VisionVerifierGateService(verifier=verifier, clock=FakeClock())
    service = ReviewGateService(task_engine=task_engine, vision_gate=vision_gate)
    return service, task_engine


async def test_deliberate_mismatch_blocks_before_done(tmp_path: Path) -> None:
    """The vision verifier flags the brief mismatch and blocks completion."""
    _write_app_screenshot(tmp_path, _DELIBERATE_RED)
    service, task_engine = _build_gate_service(tmp_path)

    result = await service.run_pipeline(
        task_id="task-gui-1",
        pipeline=ReviewPipeline(stages=(_PassingStage(),)),
        decided_by="bob",
        requested_by="bob",
        vision_input=_vision_input(),
    )

    assert result.final_verdict is ReviewVerdict.PASS  # structural pipeline passed
    call = task_engine.submit.call_args[0][0]
    assert call.target_status is TaskStatus.IN_PROGRESS  # vision rerouted to rework
    assert "Vision review blocked" in call.reason


async def test_matching_app_completes(tmp_path: Path) -> None:
    """A control run whose UI matches the brief proceeds to COMPLETED."""
    _write_app_screenshot(tmp_path, _MATCHING_BLUE)
    service, task_engine = _build_gate_service(tmp_path)

    await service.run_pipeline(
        task_id="task-gui-1",
        pipeline=ReviewPipeline(stages=(_PassingStage(),)),
        decided_by="bob",
        requested_by="bob",
        vision_input=_vision_input(),
    )

    call = task_engine.submit.call_args[0][0]
    assert call.target_status is TaskStatus.COMPLETED
