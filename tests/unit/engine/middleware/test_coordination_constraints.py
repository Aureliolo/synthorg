"""Tests for coordination constraint middleware."""

from datetime import UTC, date, datetime
from typing import cast
from uuid import uuid4

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.models import (
    CoordinationContext,
    CoordinationPhaseResult,
)
from synthorg.engine.decomposition.models import (
    DecompositionResult,
    SubtaskStatusRollup,
)
from synthorg.engine.middleware.coordination_constraints import (
    MagenticReplanHook,
    NoOpReplanHook,
    PlanReviewGateMiddleware,
    ProgressLedgerMiddleware,
    ReplanMiddleware,
    TaskLedgerMiddleware,
)
from synthorg.engine.middleware.coordination_protocol import (
    CoordinationMiddleware,
    CoordinationMiddlewareContext,
)
from synthorg.engine.middleware.errors import PlanReviewGatedError
from synthorg.engine.middleware.models import (
    ProgressLedger,
    TaskLedger,
)
from tests._shared import as_uuid
from tests.unit.engine.conftest import make_decomposition, make_subtask

# ── Test helpers ──────────────────────────────────────────────────


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(
            provider="test-provider",
            model_id="test-small-001",
        ),
        hiring_date=date(2026, 1, 1),
    )


def _task() -> Task:
    return Task(
        id=as_uuid("task-1"),
        title="Test task",
        description="A detailed test task description",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="test-project",
        created_by="test-creator",
        acceptance_criteria=(
            AcceptanceCriterion(
                description="All unit tests pass",
            ),
        ),
    )


def _coord_context() -> CoordinationContext:
    return CoordinationContext(
        task=_task(),
        available_agents=(_identity(),),
    )


def _decomp(parent_task_id: str = "parent-1") -> DecompositionResult:
    return make_decomposition(
        subtasks=(make_subtask("s1"),),
        parent_task_id=parent_task_id,
    )


def _rollup(
    *,
    parent_task_id: str = "parent-1",
    completed: int = 0,
    total: int = 1,
) -> SubtaskStatusRollup:
    return SubtaskStatusRollup(
        parent_task_id=NotBlankStr(parent_task_id),
        total=total,
        completed=completed,
        failed=0,
        in_progress=max(total - completed, 0),
        blocked=0,
        cancelled=0,
    )


def _mw_context(
    *,
    decomp_result: DecompositionResult | None = None,
    status_rollup: SubtaskStatusRollup | None = None,
    phases: tuple[CoordinationPhaseResult, ...] = (),
    task_ledger: TaskLedger | None = None,
    progress_ledger: ProgressLedger | None = None,
) -> CoordinationMiddlewareContext:
    return CoordinationMiddlewareContext(
        coordination_context=_coord_context(),
        decomposition_result=decomp_result,
        status_rollup=status_rollup,
        phases=phases,
        task_ledger=task_ledger,
        progress_ledger=progress_ledger,
    )


# ── TaskLedgerMiddleware ──────────────────────────────────────────


@pytest.mark.unit
class TestTaskLedgerMiddleware:
    """TaskLedgerMiddleware creates TaskLedger from decomposition."""

    def test_satisfies_protocol(self) -> None:
        mw = TaskLedgerMiddleware()
        assert isinstance(mw, CoordinationMiddleware)

    def test_name(self) -> None:
        assert TaskLedgerMiddleware().name == "task_ledger"

    async def test_no_decomposition_passthrough(self) -> None:
        mw = TaskLedgerMiddleware()
        ctx = _mw_context(decomp_result=None)
        result = await mw.before_dispatch(ctx)
        assert result.task_ledger is None

    async def test_creates_ledger(self) -> None:
        mw = TaskLedgerMiddleware()
        ctx = _mw_context(decomp_result=_decomp())
        result = await mw.before_dispatch(ctx)
        assert result.task_ledger is not None
        assert result.task_ledger.plan_version == 1
        assert len(result.task_ledger.known_facts) > 0

    async def test_increments_version(self) -> None:
        mw = TaskLedgerMiddleware()
        existing = TaskLedger(
            plan_text="old plan",
            plan_version=2,
            created_at=datetime.now(UTC),
        )
        ctx = _mw_context(
            decomp_result=_decomp(),
            task_ledger=existing,
        )
        result = await mw.before_dispatch(ctx)
        assert result.task_ledger is not None
        assert result.task_ledger.plan_version == 3


# ── ProgressLedgerMiddleware ──────────────────────────────────────


@pytest.mark.unit
class TestProgressLedgerMiddleware:
    """ProgressLedgerMiddleware emits ProgressLedger after rollup."""

    def test_satisfies_protocol(self) -> None:
        mw = ProgressLedgerMiddleware()
        assert isinstance(mw, CoordinationMiddleware)

    def test_name(self) -> None:
        assert ProgressLedgerMiddleware().name == "progress_ledger"

    async def test_first_round_with_progress(self) -> None:
        mw = ProgressLedgerMiddleware()
        ctx = _mw_context(status_rollup=_rollup(completed=2, total=3))
        result = await mw.after_rollup(ctx)
        assert result.progress_ledger is not None
        assert result.progress_ledger.round_number == 1
        assert result.progress_ledger.progress_made is True
        assert result.progress_ledger.stall_count == 0
        assert result.progress_ledger.next_action == "continue"

    async def test_first_round_no_completed_count_no_progress(self) -> None:
        mw = ProgressLedgerMiddleware()
        ctx = _mw_context(status_rollup=_rollup(completed=0, total=3))
        result = await mw.after_rollup(ctx)
        assert result.progress_ledger is not None
        assert result.progress_ledger.round_number == 1
        assert result.progress_ledger.progress_made is False
        assert result.progress_ledger.stall_count == 1

    async def test_stall_increments(self) -> None:
        mw = ProgressLedgerMiddleware()
        existing = ProgressLedger(
            round_number=2,
            progress_made=False,
            stall_count=1,
            next_action="replan",
        )
        ctx = _mw_context(
            status_rollup=None,
            progress_ledger=existing,
        )
        result = await mw.after_rollup(ctx)
        assert result.progress_ledger is not None
        assert result.progress_ledger.round_number == 3
        assert result.progress_ledger.stall_count == 2

    async def test_escalation_after_three_stalls(self) -> None:
        mw = ProgressLedgerMiddleware()
        existing = ProgressLedger(
            round_number=3,
            progress_made=False,
            stall_count=2,
            next_action="replan",
        )
        ctx = _mw_context(
            status_rollup=None,
            progress_ledger=existing,
        )
        result = await mw.after_rollup(ctx)
        assert result.progress_ledger is not None
        assert result.progress_ledger.next_action == "escalate"

    async def test_blocking_issues_from_failed_phases(self) -> None:
        mw = ProgressLedgerMiddleware()
        phases = (
            CoordinationPhaseResult(
                phase="dispatch",
                success=False,
                duration_seconds=1.0,
                error="dispatch failed",
            ),
        )
        ctx = _mw_context(
            status_rollup=_rollup(completed=0, total=2),
            phases=phases,
        )
        result = await mw.after_rollup(ctx)
        assert result.progress_ledger is not None
        assert len(result.progress_ledger.blocking_issues) == 1


# ── NoOpReplanHook ────────────────────────────────────────────────


@pytest.mark.unit
class TestNoOpReplanHook:
    """NoOpReplanHook never replans."""

    async def test_should_replan_false(self) -> None:
        hook = NoOpReplanHook()
        ctx = _mw_context()
        assert await hook.should_replan(ctx) is False

    async def test_replan_returns_context(self) -> None:
        hook = NoOpReplanHook()
        ctx = _mw_context()
        result = await hook.replan(ctx)
        assert result is ctx


# ── MagenticReplanHook ────────────────────────────────────────────


@pytest.mark.unit
class TestMagenticReplanHook:
    """MagenticReplanHook with stall detection and caps."""

    async def test_no_progress_ledger_no_replan(self) -> None:
        hook = MagenticReplanHook(max_stall_count=3, max_reset_count=2)
        ctx = _mw_context(progress_ledger=None)
        assert await hook.should_replan(ctx) is False

    async def test_no_stall_no_replan(self) -> None:
        hook = MagenticReplanHook(max_stall_count=3, max_reset_count=2)
        progress = ProgressLedger(
            round_number=1,
            progress_made=True,
            next_action="continue",
        )
        ctx = _mw_context(progress_ledger=progress)
        assert await hook.should_replan(ctx) is False

    async def test_stall_triggers_replan(self) -> None:
        hook = MagenticReplanHook(max_stall_count=3, max_reset_count=2)
        progress = ProgressLedger(
            round_number=2,
            progress_made=False,
            stall_count=1,
            next_action="replan",
        )
        ctx = _mw_context(progress_ledger=progress)
        assert await hook.should_replan(ctx) is True

    async def test_stall_cap_blocks_replan(self) -> None:
        hook = MagenticReplanHook(max_stall_count=2, max_reset_count=2)
        progress = ProgressLedger(
            round_number=3,
            progress_made=False,
            stall_count=2,
            next_action="escalate",
        )
        ctx = _mw_context(progress_ledger=progress)
        assert await hook.should_replan(ctx) is False

    async def test_reset_cap_blocks_replan(self) -> None:
        hook = MagenticReplanHook(max_stall_count=3, max_reset_count=1)
        progress = ProgressLedger(
            round_number=2,
            progress_made=False,
            stall_count=1,
            reset_count=1,
            next_action="replan",
        )
        ctx = _mw_context(progress_ledger=progress)
        assert await hook.should_replan(ctx) is False

    async def test_replan_increments_reset(self) -> None:
        hook = MagenticReplanHook(max_stall_count=3, max_reset_count=2)
        progress = ProgressLedger(
            round_number=2,
            progress_made=False,
            stall_count=1,
            reset_count=0,
            next_action="replan",
        )
        ctx = _mw_context(progress_ledger=progress)
        result = await hook.replan(ctx)
        assert result.progress_ledger is not None
        assert result.progress_ledger.reset_count == 1


# ── ReplanMiddleware ──────────────────────────────────────────────


@pytest.mark.unit
class TestReplanMiddleware:
    """ReplanMiddleware wraps CoordinationReplanHook."""

    def test_satisfies_protocol(self) -> None:
        mw = ReplanMiddleware()
        assert isinstance(mw, CoordinationMiddleware)

    def test_name(self) -> None:
        assert ReplanMiddleware().name == "coordination_replan"

    async def test_default_noop(self) -> None:
        mw = ReplanMiddleware()
        ctx = _mw_context()
        result = await mw.after_rollup(ctx)
        assert result is ctx

    async def test_with_replan_hook(self) -> None:
        hook = MagenticReplanHook(max_stall_count=5, max_reset_count=2)
        mw = ReplanMiddleware(replan_hook=hook)
        progress = ProgressLedger(
            round_number=2,
            progress_made=False,
            stall_count=1,
            next_action="replan",
        )
        ctx = _mw_context(progress_ledger=progress)
        result = await mw.after_rollup(ctx)
        assert result.progress_ledger is not None
        assert result.progress_ledger.reset_count == 1


# ── PlanReviewGateMiddleware ──────────────────────────────────────


@pytest.mark.unit
class TestPlanReviewGateMiddleware:
    """PlanReviewGateMiddleware gates on autonomy level."""

    def test_satisfies_protocol(self) -> None:
        mw = PlanReviewGateMiddleware()
        assert isinstance(mw, CoordinationMiddleware)

    def test_name(self) -> None:
        assert PlanReviewGateMiddleware().name == "plan_review_gate"

    async def test_full_autonomy_not_gated(self) -> None:
        mw = PlanReviewGateMiddleware(
            default_autonomy_level=AutonomyLevel.FULL,
        )
        ctx = _mw_context()
        result = await mw.before_dispatch(ctx)
        meta = cast("dict[str, object]", result.metadata["plan_review_gate"])
        assert meta["gated"] is False

    async def test_supervised_gated(self) -> None:
        mw = PlanReviewGateMiddleware(
            default_autonomy_level=AutonomyLevel.SUPERVISED,
        )
        ctx = _mw_context()
        with pytest.raises(PlanReviewGatedError) as exc_info:
            await mw.before_dispatch(ctx)
        assert exc_info.value.autonomy_level == "supervised"

    async def test_locked_gated(self) -> None:
        mw = PlanReviewGateMiddleware(
            default_autonomy_level=AutonomyLevel.LOCKED,
        )
        ctx = _mw_context()
        with pytest.raises(PlanReviewGatedError) as exc_info:
            await mw.before_dispatch(ctx)
        assert exc_info.value.autonomy_level == "locked"

    async def test_semi_not_gated(self) -> None:
        mw = PlanReviewGateMiddleware(
            default_autonomy_level=AutonomyLevel.SEMI,
        )
        ctx = _mw_context()
        result = await mw.before_dispatch(ctx)
        meta = cast("dict[str, object]", result.metadata["plan_review_gate"])
        assert meta["gated"] is False
