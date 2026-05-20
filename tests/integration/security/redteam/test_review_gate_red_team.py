"""Integration test for ReviewGateService.run_pipeline with the red-team gate.

Validates the FULL acceptance criterion: a deliverable with a planted
defect, after the normal ReviewPipeline returns PASS, is BLOCKed by
the red-team gate and the task transitions IN_REVIEW -> IN_PROGRESS
(rework) instead of COMPLETED.

This is the missing end-to-end coverage flagged by the pre-PR review:
the gate test in tests/unit/security/redteam/test_gate.py validates
the gate's verdict in isolation; this file validates the ReviewGate
service consumes the verdict correctly.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import AutonomyLevel, Priority, TaskStatus, TaskType
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.engine.review import (
    ReviewPipeline,
    ReviewStageResult,
    ReviewVerdict,
)
from synthorg.engine.review_gate import ReviewGateService
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskMutationResult
from synthorg.security.redteam.errors import RedTeamDispatchError
from synthorg.security.redteam.gate import RedTeamGateService
from synthorg.security.redteam.grounding.heuristic import HeuristicGroundingChecker
from synthorg.security.redteam.models import (
    RedTeamAttackSurface,
    RedTeamFinding,
    RedTeamReport,
    RedTeamReviewInput,
    RedTeamSeverity,
)
from synthorg.security.redteam.protocol import AgentRunner
from synthorg.security.redteam.report_repo import InMemoryRedTeamReportRepository
from tests._shared import FakeClock, mock_of

pytestmark = pytest.mark.integration


def _task() -> Task:
    return Task(
        id="task-rt-1",
        title="Backend service",
        description="Task description for review-gate integration.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-rt",
        created_by="alice",
        assigned_to="agent-backend",
        status=TaskStatus.IN_REVIEW,
        acceptance_criteria=(
            AcceptanceCriterion(description="Login endpoint exposed."),
            AcceptanceCriterion(description="Password reset endpoint exposed."),
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


class _ScriptedRunner:
    """Writes a pre-built planted-defect report through the repo."""

    def __init__(
        self,
        *,
        repo: InMemoryRedTeamReportRepository,
        report: RedTeamReport,
    ) -> None:
        self._repo = repo
        self._report = report

    async def run(self, *, review_input: RedTeamReviewInput) -> None:
        await self._repo.put(
            execution_id=review_input.execution_id,
            report=self._report,
        )


class _PassingStage:
    """Stage that always returns PASS so the pipeline goes COMPLETED."""

    name = "structural-pass"

    async def execute(self, task: Task) -> ReviewStageResult:
        del task
        return ReviewStageResult(
            stage_name=self.name,
            verdict=ReviewVerdict.PASS,
            reason="Stage scripted to pass.",
        )


def _planted_report() -> RedTeamReport:
    return RedTeamReport(
        execution_id="exec-rt-1",
        task_id="task-rt-1",
        findings=(
            RedTeamFinding(
                attack_surface=RedTeamAttackSurface.REQUIREMENTS,
                severity=RedTeamSeverity.HIGH,
                description="Brief requires password reset; deliverable omits it.",
                evidence=("Brief: 'Password reset endpoint exposed.'",),
            ),
        ),
        summary="HIGH: missing password reset endpoint.",
    )


def _planted_review_input() -> RedTeamReviewInput:
    return RedTeamReviewInput(
        task_id="task-rt-1",
        execution_id="exec-rt-1",
        deliverable_content=("Backend service complete. Login endpoint is live."),
        acceptance_criteria=(
            "Login endpoint exposed.",
            "Password reset endpoint exposed.",
        ),
        assigned_agent_id="agent-backend",
        autonomy=AutonomyLevel.SUPERVISED,
    )


def _build_review_gate(*, runner: AgentRunner) -> tuple[ReviewGateService, Any]:
    """Wire a ReviewGateService with a real RedTeamGateService."""
    task = _task()
    task_engine = _mock_task_engine(task)
    if isinstance(runner, _ScriptedRunner):
        repo = runner._repo
    else:
        repo = InMemoryRedTeamReportRepository()
    red_team_gate = RedTeamGateService(
        agent_runner=runner,
        report_repo=repo,
        grounding_checker=HeuristicGroundingChecker(),
        clock=FakeClock(),
    )
    service = ReviewGateService(
        task_engine=task_engine,
        red_team_gate=red_team_gate,
    )
    return service, task_engine


async def test_planted_defect_blocks_review_gate_and_routes_to_in_progress() -> None:
    """End-to-end acceptance: planted HIGH finding routes the task to IN_PROGRESS."""
    repo = InMemoryRedTeamReportRepository()
    runner: AgentRunner = _ScriptedRunner(repo=repo, report=_planted_report())
    service, task_engine = _build_review_gate(runner=runner)

    pipeline = ReviewPipeline(stages=(_PassingStage(),))
    result = await service.run_pipeline(
        task_id="task-rt-1",
        pipeline=pipeline,
        decided_by="bob",
        requested_by="bob",
        red_team_input=_planted_review_input(),
    )

    assert result.final_verdict is ReviewVerdict.PASS  # the structural pipeline passed
    task_engine.submit.assert_called_once()
    call = task_engine.submit.call_args[0][0]
    assert call.target_status is TaskStatus.IN_PROGRESS  # red-team rerouted to rework
    assert "Red-team review blocked" in call.reason


async def test_red_team_gate_skipped_when_review_input_missing() -> None:
    """No ``red_team_input`` means the gate is skipped; PASS proceeds to COMPLETED."""
    repo = InMemoryRedTeamReportRepository()
    runner: AgentRunner = _ScriptedRunner(repo=repo, report=_planted_report())
    service, task_engine = _build_review_gate(runner=runner)

    pipeline = ReviewPipeline(stages=(_PassingStage(),))
    await service.run_pipeline(
        task_id="task-rt-1",
        pipeline=pipeline,
        decided_by="bob",
        requested_by="bob",
        # no red_team_input provided
    )

    call = task_engine.submit.call_args[0][0]
    assert call.target_status is TaskStatus.COMPLETED


async def test_red_team_gate_absent_no_change() -> None:
    """Without a configured ``red_team_gate``, behaviour is unchanged."""
    task = _task()
    task_engine = _mock_task_engine(task)
    service = ReviewGateService(task_engine=task_engine)  # no gate

    pipeline = ReviewPipeline(stages=(_PassingStage(),))
    await service.run_pipeline(
        task_id="task-rt-1",
        pipeline=pipeline,
        decided_by="bob",
        requested_by="bob",
        red_team_input=_planted_review_input(),
    )

    call = task_engine.submit.call_args[0][0]
    assert call.target_status is TaskStatus.COMPLETED


class _DispatchFailingRunner:
    """Runner that always raises ``RedTeamDispatchError`` (production-like)."""

    async def run(self, *, review_input: RedTeamReviewInput) -> None:
        del review_input
        cause_msg = "provider down"
        dispatch_msg = "dispatch failed"
        try:
            raise RuntimeError(cause_msg)  # noqa: TRY301 -- mirrors production runner
        except Exception as exc:
            raise RedTeamDispatchError(dispatch_msg) from exc


async def test_red_team_dispatch_failure_does_not_block_completion() -> None:
    """Agent dispatch failure must fail-OPEN; deliverable proceeds to COMPLETED."""
    runner: AgentRunner = _DispatchFailingRunner()
    service, task_engine = _build_review_gate(runner=runner)

    pipeline = ReviewPipeline(stages=(_PassingStage(),))
    await service.run_pipeline(
        task_id="task-rt-1",
        pipeline=pipeline,
        decided_by="bob",
        requested_by="bob",
        red_team_input=_planted_review_input(),
    )

    call = task_engine.submit.call_args[0][0]
    # Fail-OPEN synthetic INFO finding does NOT block; task completes.
    assert call.target_status is TaskStatus.COMPLETED
