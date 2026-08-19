"""A plan this process is writing is never reaped as abandoned.

Recovery fails a plan left at PLANNING, because a shell nobody will ever fill
is a state with no exit. But the row says PLANNING for the whole time
decomposition, the review panel and its revision rounds are writing it, so
"still being written" and "was being written when the process died" are the
same row: only the process holding it can tell them apart.

The live-run ledger is where that knowledge lives, and the invariant pinned
here is that the plan-review gate holds a claim over exactly the window in
which the row says PLANNING because this process is the reason. It is taken
when the shell is persisted and dropped on every route back out, so a sweep
either sees a claim or sees a shell nobody is filling, and never has to guess.

A live run spent 642 seconds on a panel and two revision rounds. The sweep
runs every 600.
"""

from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_helpers.plan_review_wiring import PlanReviewApprovalGate
from synthorg.api.services.plan_service import PlanService
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.plan_review import PlanReviewOutcome
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStructure, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.run_ledger import LiveRunLedger
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.pipeline.models import WorkItem, WorkSource
from synthorg.engine.run_recovery.reconciler import RunRecoveryReconciler
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskMutationResult
from tests._shared import FakeClock, as_uuid, mock_of, sid
from tests.unit.api.fakes import FakeLifecycleTransitionRepository
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit

_NO_PANEL = PlanReviewOutcome(
    absent_reason=NotBlankStr("no stakeholder panel is attached")
)


def _task(label: str) -> Task:
    return Task(
        id=as_uuid(label),
        title=f"Task {label}",
        description=f"A detailed description for {label}",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="beachhead",
        created_by="ceo",
    )


def _work_item() -> WorkItem:
    return WorkItem(
        origin_adapter_id=NotBlankStr("charter-dispatch"),
        source=WorkSource.OBJECTIVE,
        title=NotBlankStr("Ship the game"),
        raw_intent=NotBlankStr("Build a playable single-player game"),
        project=NotBlankStr("beachhead"),
        requested_by=NotBlankStr("ceo"),
        plan_required=True,
        charter_id=sid("charter-1"),
    )


def _decomposition() -> DecompositionResult:
    return DecompositionResult(
        plan=DecompositionPlan(
            parent_task_id=sid("root"),
            subtasks=(
                SubtaskDefinition(
                    id=sid("sub-1"),
                    title="A",
                    description="Board grid",
                    acceptance_criteria=(NotBlankStr("board renders"),),
                    expected_artifacts=(NotBlankStr("src/board.py"),),
                ),
            ),
            task_structure=TaskStructure.SEQUENTIAL,
        ),
        created_tasks=(_task("sub-1"),),
    )


def _gate(
    backend: FakePersistenceBackend, ledger: LiveRunLedger
) -> PlanReviewApprovalGate:
    clock = FakeClock()
    return PlanReviewApprovalGate(
        approval_store=ApprovalStore(),
        plans=PlanService(
            repo=backend.plans,
            clock=clock,
            transitions=FakeLifecycleTransitionRepository(),
            projects=backend.projects,
        ),
        tasks=backend.tasks,
        projects=backend.projects,
        clock=clock,
        ledger=ledger,
    )


def _reconciler(
    backend: FakePersistenceBackend,
    ledger: LiveRunLedger,
    *,
    driven: list[str],
) -> RunRecoveryReconciler:
    async def _drive(plan: Plan) -> bool:
        driven.append(str(plan.id))
        return True

    async def _recompute(plan: Plan) -> None:
        driven.append(str(plan.id))

    return RunRecoveryReconciler(
        persistence=backend,
        task_engine=mock_of[TaskEngine](
            submit=AsyncMock(
                return_value=TaskMutationResult(request_id="r", success=True, version=2)
            )
        ),
        ledger=ledger,
        drive_plan=_drive,
        recompute_plan=_recompute,
    )


async def _opened_shell(
    backend: FakePersistenceBackend, ledger: LiveRunLedger
) -> tuple[PlanReviewApprovalGate, Task, UUID]:
    """Persist a PLANNING shell the way the pipeline does at greenlight.

    Returns:
        The gate, the objective task under it, and the shell's id.
    """
    root = _task("root")
    await backend.tasks.save(root)
    gate = _gate(backend, ledger)
    plan_id = await gate.open_plan(work_item=_work_item(), task=root)
    return gate, root, plan_id


class TestRecoveryNeverReapsAPlanBeingWritten:
    async def test_a_shell_still_being_written_survives_a_sweep(self) -> None:
        backend = FakePersistenceBackend()
        ledger = LiveRunLedger()
        _, _, plan_id = await _opened_shell(backend, ledger)

        report = await _reconciler(backend, ledger, driven=[]).reconcile(
            trigger="periodic"
        )

        assert report.failed == 0
        assert report.skipped == 1
        surviving = await backend.plans.get(NotBlankStr(str(plan_id)))
        assert surviving is not None
        assert surviving.status is PlanStatus.PLANNING

    async def test_a_shell_nobody_is_writing_is_still_failed(self) -> None:
        # The complement, and the reason the sweep exists: a claim covering
        # the writing window must not blind recovery to a shell whose writer
        # is gone, which after a restart is every shell it can see.
        backend = FakePersistenceBackend()
        _, _, plan_id = await _opened_shell(backend, LiveRunLedger())

        report = await _reconciler(backend, LiveRunLedger(), driven=[]).reconcile(
            trigger="boot"
        )

        assert report.failed == 1
        reaped = await backend.plans.get(NotBlankStr(str(plan_id)))
        assert reaped is not None
        assert reaped.status is PlanStatus.FAILED


class TestTheClaimCoversExactlyTheWritingWindow:
    async def test_filling_the_shell_drops_the_claim(self) -> None:
        # A claim outliving the write is the mirror defect: the plan becomes
        # permanently invisible to recovery for the life of the process.
        backend = FakePersistenceBackend()
        ledger = LiveRunLedger()
        gate, root, plan_id = await _opened_shell(backend, ledger)

        await gate.request_plan_approval(
            plan_id=plan_id,
            work_item=_work_item(),
            task=root,
            plan=_decomposition(),
            review=_NO_PANEL,
        )

        assert not ledger.is_driving(str(plan_id))

    async def test_failing_the_shell_drops_the_claim(self) -> None:
        backend = FakePersistenceBackend()
        ledger = LiveRunLedger()
        gate, _, plan_id = await _opened_shell(backend, ledger)

        await gate.fail_plan(plan_id=plan_id, reason="decomposition failed")

        assert not ledger.is_driving(str(plan_id))

    async def test_releasing_a_plan_never_claimed_is_harmless(self) -> None:
        # ``fail_plan`` is the compensation on paths that never reached
        # ``open_plan``, so it releases a claim it may never have taken.
        backend = FakePersistenceBackend()
        ledger = LiveRunLedger()
        gate = _gate(backend, ledger)

        gate.release_plan(as_uuid("never-opened"))

        assert len(ledger) == 0
