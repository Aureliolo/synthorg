"""Nothing unfinished is left with nobody driving it.

The invariant this pins is the one a restart broke: for every plan that has
not reached a terminal status, SOMETHING has to be able to move it. A plan's
waves are driven by a background task, and a background task does not survive
the process it lives in, so after a restart the answer for a dispatched plan
was nobody, for ever, while the board went on showing work in flight.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from synthorg.core.persistence_errors import PersistenceVersionConflictError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import (
    STAGE_STATUSES,
    TERMINAL_STATUSES,
    PlanItemKind,
    PlanStatus,
)
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.run_ledger import LiveRunLedger
from synthorg.engine.initiative.head_stages import SKELETON_ACTOR
from synthorg.engine.initiative.item_progress import TASK_PAGE_SIZE
from synthorg.engine.initiative.ports import DriveOutcome
from synthorg.engine.run_recovery.reconciler import (
    AWAITING_HUMAN_STATUSES,
    DRIVEN_STATUSES,
    RECOVERY_ACTOR,
    STAGING_STATUSES,
    UNFILLED_STATUSES,
    RunRecoveryReconciler,
)
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskMutationResult
from synthorg.persistence.plan_protocol import PlanFilterSpec, PlanRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.task_protocol import TaskRepository
from tests._shared import as_uuid, mock_of

pytestmark = pytest.mark.unit


def _plan(
    *,
    status: PlanStatus,
    plan_id: UUID | None = None,
    parent_task_id: str = "parent-task",
) -> Plan:
    now = datetime(2026, 7, 19, tzinfo=UTC)
    # A FAILED plan is refused without one, and this factory builds every
    # status the enum has so the exhaustiveness tests can sweep them.
    failure_reason = (
        NotBlankStr("it did not deliver") if status is PlanStatus.FAILED else None
    )
    return Plan(
        failure_reason=failure_reason,
        id=plan_id or as_uuid("plan"),
        objective_id=NotBlankStr("objective-1"),
        objective_title=NotBlankStr("Ship the thing"),
        parent_task_id=NotBlankStr(parent_task_id),
        project=NotBlankStr(str(as_uuid("project"))),
        project_name=NotBlankStr("Platform"),
        created_at=now,
        updated_at=now,
        status=status,
        items=(
            PlanItem(
                id=NotBlankStr(str(as_uuid("item-1"))),
                title=NotBlankStr("Build it"),
                description=NotBlankStr("A detailed plan item description"),
                kind=PlanItemKind.WORK,
                acceptance_criteria=(NotBlankStr("the thing builds"),),
                expected_artifacts=(NotBlankStr("the built thing"),),
            ),
        ),
    )


def _task(
    label: str,
    *,
    status: TaskStatus,
    plan_id: UUID,
    plan_item_id: UUID | None,
    created_by: str = "coordinator",
) -> Task:
    return Task(
        id=as_uuid(label),
        title=f"Task {label}",
        description="A detailed test task description",
        type=TaskType.DEVELOPMENT,
        project="test-project",
        created_by=created_by,
        status=status,
        plan_id=plan_id,
        plan_item_id=plan_item_id,
        assigned_to=None if status is TaskStatus.CREATED else str(as_uuid("worker")),
    )


def _persistence(  # type: ignore[explicit-any]  # mock_of returns Any
    *,
    plans: list[Plan],
    tasks: list[Task] | None = None,
    saved: list[Plan] | None = None,
    update_conflicts: bool = False,
) -> Any:
    async def _update(plan: Plan, *, expected_version: int | None = None) -> None:
        if update_conflicts:
            msg = f"plan {plan.id} moved past version {expected_version}"
            raise PersistenceVersionConflictError(msg)
        if saved is not None:
            saved.append(plan)

    # Filtered rather than returning every plan: the sweep asks per unfinished
    # status, so a double ignoring the spec would answer with plans the query
    # it stands in for could not return, and every status branch below would
    # be exercised against a set production never produces.
    async def _query(
        filter_spec: PlanFilterSpec,
        *,
        limit: int = TASK_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Plan, ...]:
        matched = [
            plan
            for plan in plans
            if filter_spec.status is None or plan.status is filter_spec.status
        ]
        return tuple(matched[offset : offset + limit])

    return mock_of[PersistenceBackend](
        plans=mock_of[PlanRepository](
            query=AsyncMock(side_effect=_query),
            update=AsyncMock(side_effect=_update),
        ),
        tasks=mock_of[TaskRepository](query=AsyncMock(return_value=tasks or [])),
    )


def _engine(  # type: ignore[explicit-any]  # mock_of returns Any
    *,
    moved: list[str] | None = None,
    actors: list[str] | None = None,
) -> Any:
    async def _submit(mutation: Any) -> TaskMutationResult:  # type: ignore[explicit-any]
        if moved is not None:
            moved.append(f"{mutation.task_id}:{mutation.target_status.value}")
        if actors is not None:
            actors.append(str(mutation.requested_by))
        return TaskMutationResult(request_id="r", success=True, version=2)

    return mock_of[TaskEngine](submit=AsyncMock(side_effect=_submit))


def _reconciler(  # type: ignore[explicit-any]  # mock_of returns Any
    *,
    persistence: Any,
    engine: Any = None,
    ledger: LiveRunLedger | None = None,
    driven: list[str] | None = None,
    recomputed: list[str] | None = None,
    rejudged: list[str] | None = None,
    awaiting_a_person: frozenset[str] | None = frozenset(),
    defers_to_queue: bool = False,
) -> RunRecoveryReconciler:
    async def _drive(plan: Plan) -> DriveOutcome:
        if driven is not None:
            driven.append(str(plan.id))
        return DriveOutcome.DRIVING

    async def _recompute(plan: Plan) -> None:
        if recomputed is not None:
            recomputed.append(str(plan.id))

    async def _rejudge(task: Task) -> None:
        if rejudged is not None:
            rejudged.append(str(task.id))

    async def _open_decisions() -> frozenset[str]:
        return awaiting_a_person or frozenset()

    return RunRecoveryReconciler(
        persistence=persistence,
        task_engine=engine if engine is not None else _engine(),
        # An explicitly-passed empty ledger is a test that wants to observe
        # the claims taken on it, and ``LiveRunLedger.__len__`` makes an empty
        # one falsy, so ``or`` would silently swap in one nothing can see.
        ledger=ledger if ledger is not None else LiveRunLedger(),
        drive_plan=_drive,
        recompute_plan=_recompute,
        rejudge_task=None if rejudged is None else _rejudge,
        open_decisions=None if awaiting_a_person is None else _open_decisions,
        defers_to_queue=defers_to_queue,
    )


class TestEveryPlanStatusHasAnOwner:
    """A status this module does not name is a status nothing watches."""

    def test_the_classification_is_exhaustive(self) -> None:
        classified = (
            TERMINAL_STATUSES
            | AWAITING_HUMAN_STATUSES
            | UNFILLED_STATUSES
            | STAGING_STATUSES
            | DRIVEN_STATUSES
            | STAGE_STATUSES
        )
        assert classified == set(PlanStatus)

    def test_no_status_is_claimed_twice(self) -> None:
        # Two owners for one status is the other half of the same defect: the
        # quieter one wins and nobody is told which.
        groups = (
            TERMINAL_STATUSES,
            AWAITING_HUMAN_STATUSES,
            UNFILLED_STATUSES,
            STAGING_STATUSES,
            DRIVEN_STATUSES,
            STAGE_STATUSES,
        )
        total = sum(len(group) for group in groups)
        assert total == len(set(PlanStatus))


class TestReconcile:
    async def test_a_dispatched_plan_with_no_driver_is_resumed(self) -> None:
        plan = _plan(status=PlanStatus.EXECUTING)
        pending = _task(
            "pending",
            status=TaskStatus.CREATED,
            plan_id=plan.id,
            plan_item_id=as_uuid("item-1"),
        )
        driven: list[str] = []
        report = await _reconciler(
            persistence=_persistence(plans=[plan], tasks=[pending]),
            driven=driven,
        ).reconcile(trigger="boot")
        assert driven == [str(plan.id)]
        assert report.resumed == 1

    async def test_an_orphaned_task_is_requeued_before_the_waves_run(self) -> None:
        plan = _plan(status=PlanStatus.EXECUTING)
        stranded = _task(
            "stranded",
            status=TaskStatus.IN_PROGRESS,
            plan_id=plan.id,
            plan_item_id=as_uuid("item-1"),
        )
        moved: list[str] = []
        actors: list[str] = []
        report = await _reconciler(
            persistence=_persistence(plans=[plan], tasks=[stranded]),
            engine=_engine(moved=moved, actors=actors),
        ).reconcile(trigger="boot")
        assert moved == [f"{stranded.id}:{TaskStatus.INTERRUPTED.value}"]
        assert actors == [RECOVERY_ACTOR]
        assert report.requeued == 1

    async def test_a_finished_task_is_left_alone(self) -> None:
        plan = _plan(status=PlanStatus.EXECUTING)
        done = _task(
            "done",
            status=TaskStatus.COMPLETED,
            plan_id=plan.id,
            plan_item_id=as_uuid("item-1"),
        )
        moved: list[str] = []
        await _reconciler(
            persistence=_persistence(plans=[plan], tasks=[done]),
            engine=_engine(moved=moved),
        ).reconcile(trigger="boot")
        assert moved == []

    async def test_the_objective_task_is_not_requeued(self) -> None:
        # Its status is derived from the items by the rollup, so writing it
        # here would be a second author of one value.
        plan = _plan(status=PlanStatus.EXECUTING)
        objective = _task(
            "objective",
            status=TaskStatus.IN_PROGRESS,
            plan_id=plan.id,
            plan_item_id=None,
            created_by="charter",
        )
        moved: list[str] = []
        await _reconciler(
            persistence=_persistence(plans=[plan], tasks=[objective]),
            engine=_engine(moved=moved),
        ).reconcile(trigger="boot")
        assert moved == []

    async def test_the_assembly_task_is_requeued(self) -> None:
        # It carries no plan item either, but nothing else will ever move it:
        # the tail reads it as RUNNING for ever otherwise.
        plan = _plan(status=PlanStatus.INTEGRATING)
        assembly = _task(
            "assembly",
            status=TaskStatus.IN_PROGRESS,
            plan_id=plan.id,
            plan_item_id=None,
            created_by="initiative-integrate",
        )
        moved: list[str] = []
        await _reconciler(
            persistence=_persistence(plans=[plan], tasks=[assembly]),
            engine=_engine(moved=moved),
        ).reconcile(trigger="boot")
        assert moved == [f"{assembly.id}:{TaskStatus.INTERRUPTED.value}"]

    async def test_a_plan_with_nothing_left_to_dispatch_is_not_driven(self) -> None:
        # Watched live: the sweep re-drove one plan every tick, each drive
        # gating every wave out and changing nothing, because its rows were
        # all finished, dead, or parked on a person. The answer for a plan
        # like that is whatever the rollup derives, not another wave sweep.
        plan = _plan(status=PlanStatus.EXECUTING)
        settled = _task(
            "settled",
            status=TaskStatus.FAILED,
            plan_id=plan.id,
            plan_item_id=as_uuid("item-1"),
        )
        driven: list[str] = []
        recomputed: list[str] = []
        report = await _reconciler(
            persistence=_persistence(plans=[plan], tasks=[settled]),
            driven=driven,
            recomputed=recomputed,
        ).reconcile(trigger="periodic")
        assert driven == []
        assert recomputed == [str(plan.id)]
        assert report.recomputed == 1

    async def test_an_approved_plan_is_staged_never_driven(self) -> None:
        """An approved plan has no contract yet, so it has nothing to drive.

        This is the shape the contract stage is supposed to make impossible:
        approval writes APPROVED several awaits before it writes SKELETON, so
        a restart in that window leaves a durable APPROVED plan with items and
        no rows. Read as "everything left to dispatch" it went to the driver,
        which ran every wave against a contract nothing had written, and the
        transition table refusing APPROVED -> EXECUTING did not stop it,
        because nothing gates dispatch on the plan's status.
        """
        plan = _plan(status=PlanStatus.APPROVED)
        driven: list[str] = []
        recomputed: list[str] = []
        saved: list[Plan] = []

        report = await _reconciler(
            persistence=_persistence(plans=[plan], tasks=[], saved=saved),
            driven=driven,
            recomputed=recomputed,
        ).reconcile(trigger="boot")

        assert driven == []
        assert [written.status for written in saved] == [PlanStatus.SKELETON]
        assert recomputed == [str(plan.id)]
        assert report.recomputed == 1

    async def test_a_contested_staging_write_leaves_the_plan_alone(self) -> None:
        """A conflict is the proof an approval request is still running.

        The sweep takes no claim that request would contend with, so the write
        is guarded on the version it read and a loss means somebody else has
        the plan. Failing it instead would destroy an initiative whose
        approval landed a second earlier.
        """
        plan = _plan(status=PlanStatus.APPROVED)
        recomputed: list[str] = []

        report = await _reconciler(
            persistence=_persistence(plans=[plan], tasks=[], update_conflicts=True),
            recomputed=recomputed,
        ).reconcile(trigger="boot")

        assert recomputed == []
        assert report.skipped == 1

    async def test_a_driver_that_declines_is_reported_as_a_skip(self) -> None:
        # Whether the plan was resumed is the DRIVER's answer, not the
        # sweep's guess. Counting the call as a resume told the operator a
        # plan whose objective task no longer exists was being rescued on
        # every pass, for ever, while nothing touched it: the one report that
        # would have shown the run was stuck said it was being fixed.
        plan = _plan(status=PlanStatus.EXECUTING)
        stranded = _task(
            "stranded",
            status=TaskStatus.CREATED,
            plan_id=plan.id,
            plan_item_id=as_uuid("item-1"),
        )
        asked: list[str] = []

        async def _decline(plan: Plan) -> DriveOutcome:
            asked.append(str(plan.id))
            return DriveOutcome.REFUSED

        async def _recompute(plan: Plan) -> None:
            del plan

        report = await RunRecoveryReconciler(
            persistence=_persistence(plans=[plan], tasks=[stranded]),
            task_engine=_engine(),
            ledger=LiveRunLedger(),
            drive_plan=_decline,
            recompute_plan=_recompute,
        ).reconcile(trigger="boot")

        assert asked == [str(plan.id)]
        assert report.resumed == 0
        assert report.skipped == 1

    async def test_a_stranded_contract_job_is_requeued(self) -> None:
        """A stage job carries a plan id and no item id, like the assembly one.

        Admitting only the assembly task is what left this row invisible: the
        stage read its own persisted IN_PROGRESS as still running on every
        later pass, so the plan sat at SKELETON with nothing driving it and no
        exit, which is the exact deadlock this module exists to prevent.
        """
        plan = _plan(status=PlanStatus.SKELETON)
        contract = _task(
            "contract",
            status=TaskStatus.IN_PROGRESS,
            plan_id=plan.id,
            plan_item_id=None,
            created_by=SKELETON_ACTOR,
        )
        moved: list[str] = []

        report = await _reconciler(
            persistence=_persistence(plans=[plan], tasks=[contract]),
            engine=_engine(moved=moved),
        ).reconcile(trigger="boot")

        assert moved == [f"{contract.id}:{TaskStatus.INTERRUPTED.value}"]
        assert report.requeued == 1

    async def test_a_foreign_row_on_the_derived_id_is_left_alone(self) -> None:
        """Provenance decides, not the id shape.

        A row carrying a plan id and no item id that this stage did not mint
        is not the stage's work, and requeueing it would hand somebody else's
        task back to a pipeline that never dispatched it.
        """
        plan = _plan(status=PlanStatus.SKELETON)
        foreign = _task(
            "foreign",
            status=TaskStatus.IN_PROGRESS,
            plan_id=plan.id,
            plan_item_id=None,
            created_by="somebody-else",
        )
        moved: list[str] = []

        await _reconciler(
            persistence=_persistence(plans=[plan], tasks=[foreign]),
            engine=_engine(moved=moved),
        ).reconcile(trigger="boot")

        assert moved == []

    async def test_a_requeued_row_makes_the_plan_worth_driving(self) -> None:
        plan = _plan(status=PlanStatus.EXECUTING)
        stranded = _task(
            "stranded",
            status=TaskStatus.IN_PROGRESS,
            plan_id=plan.id,
            plan_item_id=as_uuid("item-1"),
        )
        driven: list[str] = []
        await _reconciler(
            persistence=_persistence(plans=[plan], tasks=[stranded]),
            driven=driven,
        ).reconcile(trigger="boot")
        assert driven == [str(plan.id)]

    async def test_a_review_nobody_is_judging_is_asked_again(self) -> None:
        # The task produced its work and the session judging it went with its
        # process. Nothing watches IN_REVIEW, so without this the plan can
        # never conclude.
        plan = _plan(status=PlanStatus.EXECUTING)
        reviewed = _task(
            "reviewed",
            status=TaskStatus.IN_REVIEW,
            plan_id=plan.id,
            plan_item_id=as_uuid("item-1"),
        )
        moved: list[str] = []
        rejudged: list[str] = []
        report = await _reconciler(
            persistence=_persistence(plans=[plan], tasks=[reviewed]),
            engine=_engine(moved=moved),
            rejudged=rejudged,
        ).reconcile(trigger="boot")
        # Requeueing would pay to redo work that is finished.
        assert moved == []
        assert rejudged == [str(reviewed.id)]
        assert report.rejudged == 1

    async def test_a_review_a_person_was_asked_about_is_left_alone(self) -> None:
        # Re-running the gates here decides something a human was asked, and
        # parks a second approval beside the one already open.
        plan = _plan(status=PlanStatus.EXECUTING)
        escalated = _task(
            "escalated",
            status=TaskStatus.IN_REVIEW,
            plan_id=plan.id,
            plan_item_id=as_uuid("item-1"),
        )
        rejudged: list[str] = []
        report = await _reconciler(
            persistence=_persistence(plans=[plan], tasks=[escalated]),
            rejudged=rejudged,
            awaiting_a_person=frozenset({str(escalated.id)}),
        ).reconcile(trigger="boot")
        assert rejudged == []
        assert report.rejudged == 0

    async def test_without_a_decision_reader_no_review_is_touched(self) -> None:
        # Fail closed: unable to tell the two apart, it must not guess.
        plan = _plan(status=PlanStatus.EXECUTING)
        reviewed = _task(
            "reviewed",
            status=TaskStatus.IN_REVIEW,
            plan_id=plan.id,
            plan_item_id=as_uuid("item-1"),
        )
        rejudged: list[str] = []
        report = await _reconciler(
            persistence=_persistence(plans=[plan], tasks=[reviewed]),
            rejudged=rejudged,
            awaiting_a_person=None,
        ).reconcile(trigger="boot")
        assert rejudged == []
        assert report.rejudged == 0

    async def test_a_tail_plan_is_recomputed_not_driven(self) -> None:
        plan = _plan(status=PlanStatus.EVALUATING)
        driven: list[str] = []
        recomputed: list[str] = []
        report = await _reconciler(
            persistence=_persistence(plans=[plan]),
            driven=driven,
            recomputed=recomputed,
        ).reconcile(trigger="boot")
        assert driven == []
        assert recomputed == [str(plan.id)]
        assert report.recomputed == 1

    async def test_a_plan_awaiting_a_human_is_untouched(self) -> None:
        plan = _plan(status=PlanStatus.PENDING_REVIEW)
        driven: list[str] = []
        recomputed: list[str] = []
        report = await _reconciler(
            persistence=_persistence(plans=[plan]),
            driven=driven,
            recomputed=recomputed,
        ).reconcile(trigger="boot")
        assert driven == []
        assert recomputed == []
        assert report.skipped == 1

    async def test_a_plan_already_being_driven_is_not_driven_twice(self) -> None:
        # Two drivers assign the same subtasks, the engine refuses the
        # second, and the wave that lost fails the plan it was helping.
        plan = _plan(status=PlanStatus.EXECUTING)
        ledger = LiveRunLedger()
        assert ledger.try_claim(str(plan.id))
        driven: list[str] = []
        report = await _reconciler(
            persistence=_persistence(plans=[plan]),
            ledger=ledger,
            driven=driven,
        ).reconcile(trigger="periodic")
        assert driven == []
        assert report.skipped == 1

    async def test_a_terminal_plan_is_never_considered(self) -> None:
        driven: list[str] = []
        report = await _reconciler(
            persistence=_persistence(
                plans=[_plan(status=status) for status in TERMINAL_STATUSES]
            ),
            driven=driven,
        ).reconcile(trigger="boot")
        assert driven == []
        assert report.plans_seen == 0

    async def test_an_unfillable_shell_is_failed_with_a_reason(self) -> None:
        plan = _plan(status=PlanStatus.PLANNING)
        saved: list[Plan] = []
        report = await _reconciler(
            persistence=_persistence(plans=[plan], saved=saved),
        ).reconcile(trigger="boot")
        assert report.failed == 1
        assert len(saved) == 1
        assert saved[0].status is PlanStatus.FAILED
        assert saved[0].failure_reason is not None

    async def test_failing_a_shell_is_guarded_on_the_version_it_read(self) -> None:
        # The sweep decided from a row it read a moment ago. An unversioned
        # whole-model save commits that verdict over whatever the decomposition
        # wrote in between, which is the lost update the version guard exists
        # to refuse.
        plan = _plan(status=PlanStatus.PLANNING)
        persistence = _persistence(plans=[plan], saved=[])

        await _reconciler(persistence=persistence).reconcile(trigger="boot")

        update = persistence.plans.update
        assert isinstance(update, AsyncMock)
        assert update.await_args is not None
        assert update.await_args.kwargs["expected_version"] == plan.version

    async def test_a_shell_another_writer_moved_is_left_alone(self) -> None:
        # A version conflict IS the proof the sweep lacked: somebody is still
        # writing this shell, so it is not one nobody will ever fill.
        plan = _plan(status=PlanStatus.PLANNING)

        report = await _reconciler(
            persistence=_persistence(plans=[plan], update_conflicts=True),
        ).reconcile(trigger="boot")

        assert report.failed == 0
        assert report.skipped == 1

    async def test_a_distributed_deployment_requeues_nothing(self) -> None:
        # The work queue's redelivery already owns recovering a dead runner,
        # and the row this would move could be one a live worker is running.
        plan = _plan(status=PlanStatus.EXECUTING)
        stranded = _task(
            "stranded",
            status=TaskStatus.IN_PROGRESS,
            plan_id=plan.id,
            plan_item_id=as_uuid("item-1"),
        )
        moved: list[str] = []
        report = await _reconciler(
            persistence=_persistence(plans=[plan], tasks=[stranded]),
            engine=_engine(moved=moved),
            defers_to_queue=True,
        ).reconcile(trigger="boot")
        assert moved == []
        assert report.requeued == 0

    async def test_one_unreadable_plan_does_not_stop_the_sweep(self) -> None:
        # Every other stranded run still needs picking up.
        first = _plan(status=PlanStatus.EXECUTING, plan_id=as_uuid("plan-a"))
        second = _plan(status=PlanStatus.EXECUTING, plan_id=as_uuid("plan-b"))
        driven: list[str] = []

        async def _drive(plan: Plan) -> DriveOutcome:
            if plan.id == first.id:
                msg = "cannot read this plan"
                raise RuntimeError(msg)
            driven.append(str(plan.id))
            return DriveOutcome.DRIVING

        async def _recompute(plan: Plan) -> None:
            del plan

        reconciler = RunRecoveryReconciler(
            persistence=_persistence(plans=[first, second]),
            task_engine=_engine(),
            ledger=LiveRunLedger(),
            drive_plan=_drive,
            recompute_plan=_recompute,
        )
        report = await reconciler.reconcile(trigger="boot")
        assert driven == [str(second.id)]
        assert report.resumed == 1


class TestExtensionGraftRestartRecovery:
    """A crash between grafting an extension and dispatching it needs no new code.

    The graft writes the new ``PlanItem``s and files their ``Task`` rows
    before ever calling the driver (see ``extension_graft.graft_extension``),
    so a process killed in that window leaves ordinary CREATED task rows
    under a plan still reading EXECUTING: exactly the shape every other
    dispatched plan already resumes through.
    """

    async def test_a_grafted_but_undispatched_leaf_is_resumed(self) -> None:
        plan = _plan(status=PlanStatus.EXECUTING)
        # The original leaf already delivered and is done; only the freshly
        # grafted child, filed but never dispatched, is what the sweep must
        # pick back up.
        grafted_child = _task(
            "grafted-child",
            status=TaskStatus.CREATED,
            plan_id=plan.id,
            plan_item_id=as_uuid("item-1"),
        )
        driven: list[str] = []
        report = await _reconciler(
            persistence=_persistence(plans=[plan], tasks=[grafted_child]),
            driven=driven,
        ).reconcile(trigger="boot")

        assert driven == [str(plan.id)]
        assert report.resumed == 1


class TestLiveRunLedger:
    def test_a_second_claim_is_refused(self) -> None:
        ledger = LiveRunLedger()
        assert ledger.try_claim("plan-1")
        assert not ledger.try_claim("plan-1")

    def test_release_frees_the_claim(self) -> None:
        ledger = LiveRunLedger()
        ledger.try_claim("plan-1")
        ledger.release("plan-1")
        assert ledger.try_claim("plan-1")

    def test_release_is_idempotent(self) -> None:
        # Callers release in a ``finally`` after a claim they may never have
        # won, so a release of nothing must be harmless.
        ledger = LiveRunLedger()
        ledger.release("never-claimed")
        assert len(ledger) == 0
