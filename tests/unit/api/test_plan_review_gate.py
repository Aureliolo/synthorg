"""Unit tests for ``PlanReviewApprovalGate`` durable-plan persistence."""

import asyncio
from typing import override
from unittest.mock import AsyncMock, Mock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_helpers.plan_questions import PLAN_ID_METADATA_KEY
from synthorg.api.lifecycle_helpers.plan_review_wiring import (
    PlanReviewApprovalGate,
    wire_plan_review_gate,
)
from synthorg.api.services.plan_service import PlanService
from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.approval.questions import CLARIFY_ACTION_TYPE
from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import PlanParentTaskMissingError
from synthorg.core.persistence_errors import QueryError
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.plan_review import PlanReviewOutcome
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStructure, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.pipeline.models import WorkItem, WorkSource
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.settings.resolver import ConfigResolver
from tests._shared import FakeClock, as_uuid, make_app_state, mock_of, sid
from tests.unit.api.fakes import (
    FakeLifecycleTransitionRepository,
    FakePlanRepository,
    FakeTaskRepository,
)
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit

#: The outcome the pipeline supplies when no panel is attached. Stated rather
#: than omitted: the gate takes it as required, because a parked plan with
#: neither a review nor a reason is the blank section the type forbids.
_NO_PANEL = PlanReviewOutcome(
    absent_reason=NotBlankStr("no stakeholder panel is attached")
)


class _FailingPlanRepository(FakePlanRepository):
    """Plan repo whose ``create`` always fails, to exercise the fail path."""

    @override
    async def create(self, plan: Plan) -> None:
        msg = "boom"
        raise QueryError(msg)


class _FailingApprovalStore(ApprovalStore):
    """Approval store whose ``add`` always fails, to exercise compensation."""

    @override
    async def add(self, item: ApprovalItem) -> None:
        msg = "approval boom"
        raise QueryError(msg)


class _NthAddFailingApprovalStore(ApprovalStore):
    """Approval store whose Nth ``add`` fails, leaving earlier ones written.

    The partial shape is the one that matters: parking is several writes with
    no batch behind it, so a failure part-way leaves PENDING approvals for a
    plan that is about to be FAILED. ``raises`` covers the second shape that
    partial write takes: a cancellation is a BaseException, so it reaches a
    different handler than a store error does.
    """

    def __init__(
        self,
        *,
        fail_on: int,
        raises: type[BaseException] = QueryError,
    ) -> None:
        super().__init__()
        self._fail_on = fail_on
        self._raises = raises
        self._adds = 0

    @override
    async def add(self, item: ApprovalItem) -> None:
        self._adds += 1
        if self._adds == self._fail_on:
            msg = "approval boom"
            raise self._raises(msg)
        await super().add(item)


class _UpdateFailingPlanRepository(FakePlanRepository):
    """Plan repo whose ``update`` always fails, to exercise the double-fault."""

    @override
    async def update(self, plan: Plan, *, expected_version: int | None = None) -> None:
        msg = "update boom"
        raise QueryError(msg)


def _result_task(subtask_id: str) -> Task:
    return Task(
        id=as_uuid(subtask_id),
        title=f"Subtask {subtask_id}",
        description=f"Description for {subtask_id}",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="beachhead",
        created_by="ceo",
    )


async def _gate(
    *,
    plans: FakePlanRepository | None = None,
    approval_store: ApprovalStore | None = None,
    parent: Task | None = None,
    announced: list[Plan] | None = None,
) -> tuple[PlanReviewApprovalGate, FakePlanRepository, FakeTaskRepository]:
    """Build a gate whose parent task exists, which is the ordinary case.

    The gate refuses to park a plan whose objective task is gone, so every
    happy-path test needs the parent persisted; a test for the refusal
    simply omits it.

    Returns:
        The gate plus the plan and task repositories behind it.
    """
    tasks = FakeTaskRepository()
    if parent is not None:
        await tasks.save(parent)
    plan_repo = plans if plans is not None else FakePlanRepository()
    store = approval_store if approval_store is not None else ApprovalStore()
    clock = FakeClock()
    gate = PlanReviewApprovalGate(
        approval_store=store,
        # The gate writes plan statuses, so it holds the service that records
        # them, not the repository underneath it. The tests still assert
        # against the repository, which is where the rows land.
        plans=PlanService(
            repo=plan_repo,
            clock=clock,
            transitions=FakeLifecycleTransitionRepository(),
        ),
        tasks=tasks,
        clock=clock,
        notifier=None if announced is None else announced.append,
    )
    return gate, plan_repo, tasks


def _decomposition(
    *, open_questions: tuple[NotBlankStr, ...] = ()
) -> DecompositionResult:
    plan = DecompositionPlan(
        parent_task_id=sid("root"),
        open_questions=open_questions,
        subtasks=(
            SubtaskDefinition(
                id=sid("sub-1"),
                title="A",
                description="Board grid",
                acceptance_criteria=(NotBlankStr("board renders"),),
                expected_artifacts=(NotBlankStr("src/board.py"),),
            ),
            SubtaskDefinition(
                id=sid("sub-2"),
                title="B",
                description="Movement",
                dependencies=(sid("sub-1"),),
                acceptance_criteria=(NotBlankStr("pieces move"),),
                expected_artifacts=(NotBlankStr("src/movement.py"),),
            ),
        ),
        task_structure=TaskStructure.SEQUENTIAL,
    )
    return DecompositionResult(
        plan=plan,
        created_tasks=(_result_task("sub-1"), _result_task("sub-2")),
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
    )


class TestPlanReviewApprovalGate:
    async def test_open_plan_persists_planning_shell(self) -> None:
        task = _result_task("root")
        gate, plans, _ = await _gate(parent=task)

        plan_id = await gate.open_plan(work_item=_work_item(), task=task)

        # A first-class plan exists at greenlight, PLANNING, with no items yet.
        shell = await plans.get(NotBlankStr(str(plan_id)))
        assert shell is not None
        assert shell.status is PlanStatus.PLANNING
        assert shell.items == ()
        assert shell.parent_task_id == str(task.id)

    async def test_fills_shell_and_references_id(self) -> None:
        store = ApprovalStore()
        task = _result_task("root")
        gate, plans, _ = await _gate(approval_store=store, parent=task)
        work_item = _work_item()

        plan_id = await gate.open_plan(work_item=work_item, task=task)
        handoff = await gate.request_plan_approval(
            plan_id=plan_id,
            work_item=work_item,
            task=task,
            plan=_decomposition(),
            review=_NO_PANEL,
        )

        assert handoff.subtask_count == 2
        assert handoff.approval_id is not None
        assert handoff.plan_id == str(plan_id)

        # The shell was filled in place (same id), not re-created.
        persisted = await plans.list_items()
        assert len(persisted) == 1
        durable = persisted[0]
        assert durable.id == plan_id
        assert durable.status is PlanStatus.PENDING_REVIEW
        assert durable.parent_task_id == str(task.id)
        assert len(durable.items) == 2

        parked = await store.list_items()
        assert len(parked) == 1
        assert parked[0].metadata[PLAN_ID_METADATA_KEY] == str(durable.id)

    async def test_parking_a_plan_tells_open_viewers(self) -> None:
        # Every write here happens on a background spine, after the request
        # that started it returned, so nothing else announces them: a page open
        # during decomposition rendered the pre-decomposition snapshot next to
        # a fresh approval prompt until it was reloaded by hand.
        announced: list[Plan] = []
        task = _result_task("root")
        gate, _, _ = await _gate(parent=task, announced=announced)
        work_item = _work_item()

        plan_id = await gate.open_plan(work_item=work_item, task=task)
        await gate.request_plan_approval(
            plan_id=plan_id,
            work_item=work_item,
            task=task,
            plan=_decomposition(),
            review=_NO_PANEL,
        )

        assert [(plan.id, plan.status) for plan in announced] == [
            (plan_id, PlanStatus.PENDING_REVIEW)
        ]

    async def test_open_questions_park_as_answerable_questions(self) -> None:
        """C11: the escalation must reach a surface a human can answer on.

        The decomposer wrote its unresolved questions to a field nothing read,
        so the operator was shown a list of things the org needed and no way to
        say them. Each becomes a real ``clarify:question`` approval alongside
        the plan approval, listed and answered through the existing door.
        """
        store = ApprovalStore()
        task = _result_task("root")
        gate, _, _ = await _gate(approval_store=store, parent=task)
        work_item = _work_item()
        plan_id = await gate.open_plan(work_item=work_item, task=task)

        await gate.request_plan_approval(
            plan_id=plan_id,
            work_item=work_item,
            task=task,
            plan=_decomposition(
                open_questions=(
                    NotBlankStr("Which database?"),
                    NotBlankStr("Do we ship on mobile?"),
                )
            ),
            review=_NO_PANEL,
        )

        parked = await store.list_items()
        questions = [i for i in parked if i.action_type == CLARIFY_ACTION_TYPE]
        assert [i.description for i in questions] == [
            "Which database?",
            "Do we ship on mobile?",
        ]
        # Each points back at the plan it belongs to, which is what lets the
        # answer be written onto that plan rather than stopping at the row.
        assert {i.metadata[PLAN_ID_METADATA_KEY] for i in questions} == {str(plan_id)}
        assert {i.task_id for i in questions} == {str(task.id)}

    async def test_a_plan_with_no_open_questions_parks_only_its_approval(self) -> None:
        """The common case must not add a question queue nobody asked for."""
        store = ApprovalStore()
        task = _result_task("root")
        gate, _, _ = await _gate(approval_store=store, parent=task)
        work_item = _work_item()
        plan_id = await gate.open_plan(work_item=work_item, task=task)

        await gate.request_plan_approval(
            plan_id=plan_id,
            work_item=work_item,
            task=task,
            plan=_decomposition(),
            review=_NO_PANEL,
        )

        parked = await store.list_items()
        assert not [i for i in parked if i.action_type == CLARIFY_ACTION_TYPE]

    async def test_an_absent_review_reaches_the_persisted_plan(self) -> None:
        """C8: the operator must be told the plan carries zero quality signal.

        The reason exists to be shown at the approval gate, and it can only be
        shown if it is on the plan the gate reads back. Asserting the outcome
        rather than the column would pass with the write dropped, which is how
        the two provenance columns shipped unpersisted in the first place.
        """
        task = _result_task("root")
        gate, plans, _ = await _gate(parent=task)
        work_item = _work_item()
        plan_id = await gate.open_plan(work_item=work_item, task=task)

        await gate.request_plan_approval(
            plan_id=plan_id,
            work_item=work_item,
            task=task,
            plan=_decomposition(),
            review=PlanReviewOutcome(
                absent_reason=NotBlankStr("the panel ran and returned no verdict")
            ),
        )

        persisted = await plans.get(NotBlankStr(str(plan_id)))
        assert persisted is not None
        assert persisted.review is None
        assert persisted.review_absent_reason == "the panel ran and returned no verdict"

    async def test_failing_a_plan_tells_open_viewers(self) -> None:
        announced: list[Plan] = []
        task = _result_task("root")
        gate, _, _ = await _gate(parent=task, announced=announced)
        plan_id = await gate.open_plan(work_item=_work_item(), task=task)

        await gate.fail_plan(plan_id=plan_id, reason="decompose boom")

        assert [(plan.id, plan.status) for plan in announced] == [
            (plan_id, PlanStatus.FAILED)
        ]

    async def test_an_idempotent_failure_is_announced_once(self) -> None:
        # The second call writes nothing, so announcing again would tell a
        # viewer something changed when nothing did.
        announced: list[Plan] = []
        task = _result_task("root")
        gate, _, _ = await _gate(parent=task, announced=announced)
        plan_id = await gate.open_plan(work_item=_work_item(), task=task)

        await gate.fail_plan(plan_id=plan_id, reason="decompose boom")
        await gate.fail_plan(plan_id=plan_id, reason="decompose boom again")

        assert len(announced) == 1

    async def test_a_missing_publisher_does_not_break_the_write(self) -> None:
        # The announcement is best-effort by construction: a deployment whose
        # plugin never wired must still park plans.
        task = _result_task("root")
        gate, plans, _ = await _gate(parent=task)
        work_item = _work_item()

        plan_id = await gate.open_plan(work_item=work_item, task=task)
        await gate.request_plan_approval(
            plan_id=plan_id,
            work_item=work_item,
            task=task,
            plan=_decomposition(),
            review=_NO_PANEL,
        )

        durable = await plans.get(NotBlankStr(str(plan_id)))
        assert durable is not None
        assert durable.status is PlanStatus.PENDING_REVIEW

    async def test_fail_plan_marks_failed_with_reason(self) -> None:
        task = _result_task("root")
        gate, plans, _ = await _gate(parent=task)
        plan_id = await gate.open_plan(work_item=_work_item(), task=task)

        await gate.fail_plan(plan_id=plan_id, reason="decompose boom")

        failed = await plans.get(NotBlankStr(str(plan_id)))
        assert failed is not None
        assert failed.status is PlanStatus.FAILED
        assert failed.failure_reason == "decompose boom"
        assert failed.items == ()

    async def test_open_plan_persistence_failure_raises(self) -> None:
        task = _result_task("root")
        gate, _, _ = await _gate(plans=_FailingPlanRepository(), parent=task)

        with pytest.raises(QueryError):
            await gate.open_plan(work_item=_work_item(), task=task)

    async def test_refuses_to_park_a_plan_whose_parent_is_gone(self) -> None:
        """A task deleted mid-decomposition must not reach the review queue.

        Decomposition runs for minutes, and a delete landing in that window is
        invisible to the run itself: it completes, the plan reaches
        PENDING_REVIEW, and an operator is asked to approve nine items under a
        task that 404s. The orphan can then be neither approved nor removed.
        """
        task = _result_task("root")
        gate, plans, tasks = await _gate(parent=task)
        work_item = _work_item()
        plan_id = await gate.open_plan(work_item=work_item, task=task)

        # The delete lands while the decomposition is still running.
        await tasks.delete(str(task.id))

        with pytest.raises(PlanParentTaskMissingError):
            await gate.request_plan_approval(
                plan_id=plan_id,
                work_item=work_item,
                task=task,
                plan=_decomposition(),
                review=_NO_PANEL,
            )

        # Still the untouched shell: nothing was parked and no items landed.
        shell = await plans.get(NotBlankStr(str(plan_id)))
        assert shell is not None
        assert shell.status is PlanStatus.PLANNING

    async def test_approval_write_failure_marks_plan_failed(self) -> None:
        # The plan is filled, then the approval write fails: rather than deleting
        # the now-first-class plan, it is marked FAILED so the failure stays
        # visible in Plan Review (a retry is a fresh run, not a resurrected plan).
        task = _result_task("root")
        gate, plans, _ = await _gate(
            approval_store=_FailingApprovalStore(), parent=task
        )
        work_item = _work_item()
        plan_id = await gate.open_plan(work_item=work_item, task=task)

        with pytest.raises(QueryError):
            await gate.request_plan_approval(
                plan_id=plan_id,
                work_item=work_item,
                task=task,
                plan=_decomposition(),
                review=_NO_PANEL,
            )

        persisted = await plans.list_items()
        assert len(persisted) == 1
        assert persisted[0].status is PlanStatus.FAILED
        assert persisted[0].failure_reason == "approval-store write failed"

    async def test_a_park_that_fails_partway_leaves_no_actionable_approval(
        self,
    ) -> None:
        """An approval outliving its plan is one an operator can still act on.

        Parking is several writes with no batch behind it, so the second
        question failing used to leave the plan approval and the first
        question PENDING against a plan being marked FAILED: approve and
        reject still offered, and answering the question writing back onto
        the failed plan.
        """
        task = _result_task("root")
        # Fails on the third add: plan approval, first question, then boom.
        store = _NthAddFailingApprovalStore(fail_on=3)
        gate, plans, _ = await _gate(approval_store=store, parent=task)
        work_item = _work_item()
        plan_id = await gate.open_plan(work_item=work_item, task=task)

        with pytest.raises(QueryError):
            await gate.request_plan_approval(
                plan_id=plan_id,
                work_item=work_item,
                task=task,
                plan=_decomposition(
                    open_questions=(
                        NotBlankStr("Which database?"),
                        NotBlankStr("Do we ship on mobile?"),
                    )
                ),
                review=_NO_PANEL,
            )

        assert await store.list_items() == ()
        persisted = await plans.list_items()
        assert persisted[0].status is PlanStatus.FAILED

    async def test_a_cancelled_park_leaves_no_actionable_approval(self) -> None:
        """A shutdown mid-park takes the same shape past a different door.

        ``CancelledError`` is a BaseException, so a cancellation between two
        of these writes skips the handler that compensates for a store error:
        the approvals written so far stay PENDING against a plan left in
        PENDING_REVIEW, which is the state a human still acts on.
        """
        task = _result_task("root")
        store = _NthAddFailingApprovalStore(fail_on=3, raises=asyncio.CancelledError)
        gate, plans, _ = await _gate(approval_store=store, parent=task)
        work_item = _work_item()
        plan_id = await gate.open_plan(work_item=work_item, task=task)

        with pytest.raises(asyncio.CancelledError):
            await gate.request_plan_approval(
                plan_id=plan_id,
                work_item=work_item,
                task=task,
                plan=_decomposition(
                    open_questions=(
                        NotBlankStr("Which database?"),
                        NotBlankStr("Do we ship on mobile?"),
                    )
                ),
                review=_NO_PANEL,
            )

        assert await store.list_items() == ()
        persisted = await plans.list_items()
        assert persisted[0].status is PlanStatus.FAILED
        assert persisted[0].failure_reason == "approval parking was cancelled"

    async def test_fail_plan_write_failure_is_swallowed_not_raised(self) -> None:
        # The compensating FAILED write is the one on the failure path; if it
        # itself fails, fail_plan must NOT raise (that would reintroduce the 500
        # this whole change removes). The plan stays PLANNING and it is logged.
        task = _result_task("root")
        gate, plans, _ = await _gate(plans=_UpdateFailingPlanRepository(), parent=task)
        plan_id = await gate.open_plan(work_item=_work_item(), task=task)

        # No exception escapes.
        await gate.fail_plan(plan_id=plan_id, reason="decompose boom")

        shell = await plans.get(NotBlankStr(str(plan_id)))
        assert shell is not None
        assert shell.status is PlanStatus.PLANNING

    async def test_fail_plan_is_idempotent_on_already_failed(self) -> None:
        task = _result_task("root")
        gate, plans, _ = await _gate(parent=task)
        plan_id = await gate.open_plan(work_item=_work_item(), task=task)

        await gate.fail_plan(plan_id=plan_id, reason="first")
        first = await plans.get(NotBlankStr(str(plan_id)))
        # A second compensation (e.g. the outer pipeline guard after the
        # approval-store path already failed it) is a no-op, not a re-write.
        await gate.fail_plan(plan_id=plan_id, reason="second")
        second = await plans.get(NotBlankStr(str(plan_id)))

        assert first is not None
        assert second is not None
        assert second.version == first.version
        assert second.failure_reason == "first"

    async def test_fail_plan_missing_shell_is_noop(self) -> None:
        gate, plans, _ = await _gate(parent=_result_task("root"))
        # No shell opened; fail_plan on an unknown id must not raise.
        await gate.fail_plan(plan_id=as_uuid("ghost"), reason="boom")
        assert await plans.list_items() == ()


class TestWirePlanReviewGate:
    async def _make_state(
        self, *, required: bool, wire_deps: bool, wire_pipeline: bool = True
    ) -> tuple[AppState, Mock]:
        pipeline = mock_of[WorkPipeline](attach_plan_review_gate=Mock())
        resolver = mock_of[ConfigResolver](get_bool=AsyncMock(return_value=required))
        backend: FakePersistenceBackend | None = None
        store: ApprovalStore | None = None
        if wire_deps:
            backend = FakePersistenceBackend()
            await backend.connect()
            store = ApprovalStore()
        state = make_app_state(
            config_resolver=resolver,
            work_pipeline=pipeline if wire_pipeline else None,
            approval_store=store,
            persistence=backend,
        )
        return state, pipeline.attach_plan_review_gate

    async def test_attaches_when_required_and_wired(self) -> None:
        state, attach = await self._make_state(required=True, wire_deps=True)
        await wire_plan_review_gate(state)
        attach.assert_called_once()

    async def test_declines_naming_the_disabled_setting(self) -> None:
        state, attach = await self._make_state(required=False, wire_deps=True)
        with pytest.raises(
            SubsystemDeclinedError, match="plan_approval_required is off"
        ):
            await wire_plan_review_gate(state)
        attach.assert_not_called()

    async def test_declines_naming_the_absent_pipeline(self) -> None:
        state, attach = await self._make_state(
            required=True, wire_deps=True, wire_pipeline=False
        )
        with pytest.raises(SubsystemDeclinedError, match="no work pipeline"):
            await wire_plan_review_gate(state)
        attach.assert_not_called()

    async def test_declines_naming_the_absent_approval_store(self) -> None:
        state, attach = await self._make_state(required=True, wire_deps=False)
        with pytest.raises(SubsystemDeclinedError, match="no approval store"):
            await wire_plan_review_gate(state)
        attach.assert_not_called()
