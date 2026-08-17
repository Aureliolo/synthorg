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
    PlanReviewGateMiddleware,
    TaskLedgerMiddleware,
)
from synthorg.engine.middleware.coordination_protocol import (
    CoordinationMiddleware,
    CoordinationMiddlewareContext,
)
from synthorg.engine.middleware.errors import PlanReviewGatedError
from synthorg.engine.middleware.models import TaskLedger
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
            model_id="test-basic-001",
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
) -> CoordinationMiddlewareContext:
    return CoordinationMiddlewareContext(
        coordination_context=_coord_context(),
        decomposition_result=decomp_result,
        status_rollup=status_rollup,
        phases=phases,
        task_ledger=task_ledger,
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


# ── Stall authority ───────────────────────────────────────────────


@pytest.mark.unit
class TestNoStallAuthorityHere:
    """No coordination middleware decides whether a run is stuck.

    The middleware context used to carry a progress ledger built from
    ``existing = ctx.progress_ledger``, on a context rebuilt per
    ``coordinate()`` call, so ``existing`` was always ``None`` and the
    ledger could never count past round one. It recommended a replan into
    a hook that mutated a context nobody read. Two levels already answer
    this with the evidence: the execution loop's stagnation detector and
    the initiative rollup's ``stall_reason``.
    """

    def test_context_carries_no_progress_ledger(self) -> None:
        ctx = _mw_context(status_rollup=_rollup())
        assert not hasattr(ctx, "progress_ledger")

    def test_context_refuses_a_progress_ledger(self) -> None:
        """``extra="forbid"`` is what stops the field growing back."""
        with pytest.raises(ValueError, match="progress_ledger"):
            CoordinationMiddlewareContext(
                coordination_context=_coord_context(),
                progress_ledger={"stall_count": 1},  # type: ignore[call-arg]
            )


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
