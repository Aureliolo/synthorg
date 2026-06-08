"""Integration tests for the red-team completion gate on both paths.

Validates the FULL acceptance criterion against the *production*
completion path: a deliverable with a planted defect, after a human
approves it, is BLOCKed by the red-team gate and the task transitions
IN_REVIEW -> IN_PROGRESS (rework) instead of COMPLETED. The same routing
is exercised through the pipeline-driven ``run_pipeline`` path, plus the
``on_missing_deliverable`` posture and the background dispatch.

Unit coverage in tests/unit/security/redteam/test_gate.py validates the
gate's verdict in isolation; this file verifies the service-level
routing contract end to end.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import Priority, Stakes, TaskStatus, TaskType
from synthorg.engine.review import (
    ReviewPipeline,
    ReviewStageResult,
    ReviewVerdict,
)
from synthorg.engine.review_gate import ReviewGateService
from synthorg.engine.review_gate_inputs import DeliverableReviewInputBuilder
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskMutationResult
from synthorg.observability.background_tasks import BackgroundTaskRegistry
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
from tests._shared import FakeClock, as_uuid, mock_of

pytestmark = pytest.mark.integration


def _task() -> Task:
    return Task(
        id=as_uuid("task-rt-1"),
        title="Backend service",
        description="Task description for review-gate integration.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-rt",
        created_by="alice",
        assigned_to="agent-backend",
        status=TaskStatus.IN_REVIEW,
        # HIGH stakes so the stakes-gated red-team review fires; the gate is
        # reserved for work at or above red_team_min_stakes (default HIGH).
        stakes=Stakes.HIGH,
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
        deliverable_content="Backend service complete. Login endpoint is live.",
        acceptance_criteria=(
            "Login endpoint exposed.",
            "Password reset endpoint exposed.",
        ),
        assigned_agent_id="agent-backend",
        autonomy=AutonomyLevel.SUPERVISED,
    )


def _stub_builder(review_input: RedTeamReviewInput | None) -> Any:
    """Builder double whose ``build`` returns a fixed review input."""
    return mock_of[DeliverableReviewInputBuilder](
        build=AsyncMock(return_value=review_input),
    )


def _build_review_gate(
    *,
    runner: AgentRunner,
    builder: Any,
    on_missing_deliverable: str = "block",
    background_tasks: BackgroundTaskRegistry | None = None,
) -> tuple[ReviewGateService, Any]:
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
        red_team_input_builder=builder,
        red_team_on_missing_deliverable=on_missing_deliverable,  # type: ignore[arg-type]
        background_tasks=background_tasks,
    )
    return service, task_engine


async def test_planted_defect_blocks_via_complete_review() -> None:
    """Production path: a human approve with a planted HIGH finding reworks."""
    repo = InMemoryRedTeamReportRepository()
    runner: AgentRunner = _ScriptedRunner(repo=repo, report=_planted_report())
    service, task_engine = _build_review_gate(
        runner=runner,
        builder=_stub_builder(_planted_review_input()),
    )

    await service.complete_review(
        task_id="task-rt-1",
        requested_by="bob",
        approved=True,
        decided_by="bob",
    )

    task_engine.submit.assert_called_once()
    call = task_engine.submit.call_args[0][0]
    assert call.target_status is TaskStatus.IN_PROGRESS
    assert "Red-team review blocked" in call.reason


async def test_planted_defect_blocks_via_run_pipeline() -> None:
    """Pipeline path: a PASS pipeline + planted HIGH finding reworks."""
    repo = InMemoryRedTeamReportRepository()
    runner: AgentRunner = _ScriptedRunner(repo=repo, report=_planted_report())
    service, task_engine = _build_review_gate(
        runner=runner,
        builder=_stub_builder(_planted_review_input()),
    )

    pipeline = ReviewPipeline(stages=(_PassingStage(),))
    result = await service.run_pipeline(
        task_id="task-rt-1",
        pipeline=pipeline,
        decided_by="bob",
        requested_by="bob",
    )

    assert result.final_verdict is ReviewVerdict.PASS
    call = task_engine.submit.call_args[0][0]
    assert call.target_status is TaskStatus.IN_PROGRESS
    assert "Red-team review blocked" in call.reason


async def test_no_deliverable_blocks_when_policy_block() -> None:
    """A configured gate with no retrievable deliverable fails closed."""
    repo = InMemoryRedTeamReportRepository()
    runner: AgentRunner = _ScriptedRunner(repo=repo, report=_planted_report())
    service, task_engine = _build_review_gate(
        runner=runner,
        builder=_stub_builder(None),
        on_missing_deliverable="block",
    )

    await service.complete_review(
        task_id="task-rt-1",
        requested_by="bob",
        approved=True,
        decided_by="bob",
    )

    call = task_engine.submit.call_args[0][0]
    assert call.target_status is TaskStatus.IN_PROGRESS
    assert "could not retrieve a deliverable" in call.reason


async def test_no_deliverable_skips_when_policy_skip() -> None:
    """``skip`` posture allows completion when no deliverable is retrievable."""
    repo = InMemoryRedTeamReportRepository()
    runner: AgentRunner = _ScriptedRunner(repo=repo, report=_planted_report())
    service, task_engine = _build_review_gate(
        runner=runner,
        builder=_stub_builder(None),
        on_missing_deliverable="skip",
    )

    await service.complete_review(
        task_id="task-rt-1",
        requested_by="bob",
        approved=True,
        decided_by="bob",
    )

    call = task_engine.submit.call_args[0][0]
    assert call.target_status is TaskStatus.COMPLETED


async def test_red_team_gate_absent_no_change() -> None:
    """Without a configured gate, an approve completes unchanged."""
    task = _task()
    task_engine = _mock_task_engine(task)
    service = ReviewGateService(task_engine=task_engine)  # no gate

    await service.complete_review(
        task_id="task-rt-1",
        requested_by="bob",
        approved=True,
        decided_by="bob",
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
    service, task_engine = _build_review_gate(
        runner=runner,
        builder=_stub_builder(_planted_review_input()),
    )

    await service.complete_review(
        task_id="task-rt-1",
        requested_by="bob",
        approved=True,
        decided_by="bob",
    )

    call = task_engine.submit.call_args[0][0]
    assert call.target_status is TaskStatus.COMPLETED


async def test_dispatch_completion_backgrounds_gated_approval() -> None:
    """A gated approve is dispatched to the background and still reworks."""
    repo = InMemoryRedTeamReportRepository()
    runner: AgentRunner = _ScriptedRunner(repo=repo, report=_planted_report())
    registry = BackgroundTaskRegistry(owner="test.review_gate")
    service, task_engine = _build_review_gate(
        runner=runner,
        builder=_stub_builder(_planted_review_input()),
        background_tasks=registry,
    )

    dispatched = await service.dispatch_completion(
        task_id="task-rt-1",
        requested_by="bob",
        approved=True,
        decided_by="bob",
    )
    assert dispatched is True
    # The blocking gate runs in the background task, not inline: no rework
    # transition is submitted until the registry drains.
    assert task_engine.submit.call_count == 0
    await registry.drain()

    call = task_engine.submit.call_args[0][0]
    assert call.target_status is TaskStatus.IN_PROGRESS
    assert "Red-team review blocked" in call.reason


async def test_dispatch_completion_runs_reject_inline() -> None:
    """A reject runs inline (no gate latency) and reworks immediately."""
    repo = InMemoryRedTeamReportRepository()
    runner: AgentRunner = _ScriptedRunner(repo=repo, report=_planted_report())
    registry = BackgroundTaskRegistry(owner="test.review_gate")
    service, task_engine = _build_review_gate(
        runner=runner,
        builder=_stub_builder(_planted_review_input()),
        background_tasks=registry,
    )

    dispatched = await service.dispatch_completion(
        task_id="task-rt-1",
        requested_by="bob",
        approved=False,
        decided_by="bob",
        reason="needs more tests",
    )
    assert dispatched is False
    call = task_engine.submit.call_args[0][0]
    assert call.target_status is TaskStatus.IN_PROGRESS
