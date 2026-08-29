"""Unit tests for the plan-approval resume staging branch."""

import asyncio
from datetime import UTC, datetime
from typing import Any, Final, NotRequired, TypedDict
from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers._plan_resume_writes import sync_plan_status
from synthorg.api.controllers._plan_review_resume import (
    _DISPATCH_ACTOR,
    try_plan_review_resume,
)
from synthorg.api.lifecycle_helpers.plan_questions import PLAN_ID_METADATA_KEY
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.approval.plan_review import PLAN_APPROVAL_ACTION_TYPE
from synthorg.approval.questions import (
    CLARIFY_ACTION_TYPE,
    DECISION_ACTION_TYPE,
    QUESTION_ACTION_TYPES,
)
from synthorg.core.approval import ApprovalItem
from synthorg.core.lifecycle_transition import LifecycleEntityKind
from synthorg.core.persistence_errors import QueryError
from synthorg.core.plan import Plan, PlanItem, PlanOption
from synthorg.core.plan_enums import PlanItemKind, PlanStatus
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import (
    CoordinationTopology,
    Priority,
    TaskStatus,
    TaskStructure,
    TaskType,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.rollup import ProjectRollupService
from synthorg.engine.state import EngineStateSlice
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry import AgentRegistryService
from synthorg.workers.state import RuntimeStateSlice
from tests._shared import as_uuid, make_app_state, mock_of, sid
from tests._shared.scripted_provider import make_e2e_identity
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit

#: Configured ``mock_of`` instance, typed loosely so the ``unittest.mock``
#: assertion API (``assert_awaited_once`` / ``await_args``) type-checks.
_Configured = Any  # type: ignore[explicit-any]

_NOW = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
_PLAN_ID = "plan-1"
_QUESTION = "Which database should the leaderboard use?"
_SUB_IDS = (str(as_uuid("sub-1")), str(as_uuid("sub-2")))

#: Comfortably more rows than any fixture here files, so a listing that has to
#: see all of them is not silently truncated by the default page size.
_A_FULL_PAGE = 100


def _transitions_of(engine: _Configured, task_id: str) -> list[TaskStatus]:
    """Every status *task_id* was transitioned to, in order.

    Asserted per task rather than over the whole mock, because a failed
    dispatch now moves more than one row: the parent to FAILED and each child
    the dispatch had already filed to a terminal, so "the only call" and "the
    last call" both stopped identifying the parent's.

    Args:
        engine: The task-engine double.
        task_id: The task whose transitions to collect.

    Returns:
        The target statuses, in call order.
    """
    return [
        call.args[1]
        for call in engine.transition_task.await_args_list
        if call.args[0] == task_id
    ]


async def _filed_children(backend: FakePersistenceBackend) -> list[Task]:
    """Every child row the approval path made durable, in item order.

    Read from the repository rather than from a call argument, because the
    repository is where the rollup, the recovery sweep and the dashboard all
    look: a child that exists only inside a call is one that never happened.

    Returns:
        The filed child tasks, ordered by their plan item.
    """
    children = [
        task
        for task in await backend.tasks.list_items(limit=_A_FULL_PAGE)
        if task.plan_item_id is not None
    ]
    return sorted(children, key=lambda task: _SUB_IDS.index(str(task.plan_item_id)))


def _task(label: str, *, status: TaskStatus = TaskStatus.ASSIGNED) -> Task:
    return Task(
        id=as_uuid(label),
        title=f"Task {label}",
        description=f"Description for {label}",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=sid("proj-1"),
        created_by="manager",
        assigned_to=str(as_uuid("agent-1")),
        status=status,
    )


_DECISION_ID: Final[str] = sid("decide-stack")
_RECOMMENDED_OPTION: Final[str] = "vanilla"


def _decision_item() -> PlanItem:
    """A decision item nobody has clicked, offering a recommended option."""
    return PlanItem(
        id=NotBlankStr(_DECISION_ID),
        title=NotBlankStr("Pick the stack"),
        description=NotBlankStr("Gates every implementation item"),
        acceptance_criteria=(NotBlankStr("a stack is chosen"),),
        kind=PlanItemKind.DECISION,
        options=(
            PlanOption(
                id=NotBlankStr(_RECOMMENDED_OPTION),
                title=NotBlankStr("Plain HTML and JS"),
                summary=NotBlankStr("No build step; simplest to debug"),
                recommended=True,
            ),
            PlanOption(
                id=NotBlankStr("framework"),
                title=NotBlankStr("A frontend framework"),
                summary=NotBlankStr("More structure, more build complexity"),
            ),
        ),
    )


def _durable_plan(
    parent_label: str,
    *,
    open_questions: tuple[NotBlankStr, ...] = (),
    with_decision: bool = False,
) -> Plan:
    """Build a durable two-item plan parented at *parent_label*."""
    items = tuple(
        PlanItem(
            id=NotBlankStr(sub_id),
            title=NotBlankStr(f"Subtask {n}"),
            description=NotBlankStr(f"Do part {n}"),
            acceptance_criteria=(NotBlankStr(f"part {n} done"),),
            expected_artifacts=(NotBlankStr(f"src/part_{n}.py"),),
            dependencies=(NotBlankStr(_DECISION_ID),) if with_decision else (),
        )
        for n, sub_id in enumerate(_SUB_IDS)
    )
    if with_decision:
        items = (_decision_item(), *items)
    return Plan(
        id=as_uuid(_PLAN_ID),
        project=NotBlankStr(sid("proj-1")),
        project_name=NotBlankStr("Games"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship the game"),
        parent_task_id=NotBlankStr(str(as_uuid(parent_label))),
        items=items,
        task_structure=TaskStructure.PARALLEL,
        coordination_topology=CoordinationTopology.CENTRALIZED,
        status=PlanStatus.PENDING_REVIEW,
        open_questions=open_questions,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _approval(
    approval_id: str,
    *,
    source: ApprovalSource = ApprovalSource.PLAN_REVIEW,
    action_type: str = PLAN_APPROVAL_ACTION_TYPE,
    task_id: str | None,
    plan_id: str | None,
) -> ApprovalItem:
    metadata: dict[str, str] = {}
    if plan_id is not None:
        metadata[PLAN_ID_METADATA_KEY] = plan_id
    # A question's description IS the question, and is what the plan's open
    # list is matched on when it settles.
    description = _QUESTION if action_type in QUESTION_ACTION_TYPES else "2 subtask(s)"
    return ApprovalItem(
        id=as_uuid(approval_id),
        action_type=NotBlankStr(action_type),
        title=NotBlankStr("Approve plan"),
        description=NotBlankStr(description),
        requested_by=NotBlankStr("user-1"),
        risk_level=ApprovalRiskLevel.MEDIUM,
        source=source,
        status=ApprovalStatus.PENDING,
        created_at=_NOW,
        task_id=NotBlankStr(task_id) if task_id is not None else None,
        metadata=metadata,
    )


_UNSET: Any = object()  # type: ignore[explicit-any]


class _PreconditionBranch(TypedDict):
    """One staging precondition, as the ``_seed`` overrides that trip it."""

    task: Task | None
    save_project: NotRequired[bool]


async def _seed(
    *,
    source: ApprovalSource = ApprovalSource.PLAN_REVIEW,
    action_type: str = PLAN_APPROVAL_ACTION_TYPE,
    task: Task | None,
    plan: Plan | None,
    filing_error: Exception | None = None,
    approval_task_id: str | None = _UNSET,
    save_plan: bool = True,
    save_project: bool = True,
) -> tuple[AppState, _Configured, FakePersistenceBackend]:
    """Stand up the approval path with nothing wired that it does not use.

    No coordinator, deliberately. Approval runs no wave, so wiring one here
    would let a test pass while the path secretly reached for it, and the tests
    below could not tell the two shapes apart.

    Returns:
        The app state, the task-engine double, and the persistence backend.
    """
    resolved_task_id = (
        (str(task.id) if task is not None else None)
        if approval_task_id is _UNSET
        else approval_task_id
    )
    plan_id = str(plan.id) if plan is not None else None
    store = ApprovalStore()
    await store.add(
        _approval(
            "appr-1",
            source=source,
            action_type=action_type,
            task_id=resolved_task_id,
            plan_id=plan_id,
        )
    )
    backend = FakePersistenceBackend()
    await backend.connect()
    if plan is not None and save_plan:
        await backend.plans.save(plan)
    if save_project:
        # Dispatch follows a greenlight, so the project always exists by the
        # time a plan is approved. Without it the link write fails and the
        # dispatch is refused, which is a different scenario entirely.
        await backend.projects.save(
            Project(id=as_uuid("proj-1"), name=NotBlankStr("Initiative"))
        )
    if filing_error is not None:
        # Filing the rebuilt tree is the last write the approval path makes,
        # and staging is persistence writes and nothing else, so this is the
        # one failure a test can inject without a collaborator to reach for.
        backend.tasks.save_many = AsyncMock(  # type: ignore[method-assign]
            side_effect=filing_error
        )
    engine = mock_of[TaskEngine](
        get_task=AsyncMock(return_value=task),
        transition_task=AsyncMock(return_value=None),
    )
    registry = mock_of[AgentRegistryService](
        list_active=AsyncMock(return_value=(make_e2e_identity(),))
    )
    state = make_app_state(
        approval_store=store,
        task_engine=engine,
        agent_registry=registry,
        persistence=backend,
    )
    return state, engine, backend


class TestPlanReviewResume:
    async def test_non_plan_source_is_inert(self) -> None:
        state, _, backend = await _seed(
            source=ApprovalSource.REVIEW_GATE,
            task=_task("parent-1"),
            plan=_durable_plan("parent-1"),
        )
        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )
        assert handled is False
        assert await _filed_children(backend) == []

    @pytest.mark.parametrize(
        "question_type", [CLARIFY_ACTION_TYPE, DECISION_ACTION_TYPE]
    )
    async def test_a_question_parked_off_the_plan_settles_and_builds_nothing(
        self, question_type: str
    ) -> None:
        """Answering a question settles it; it never approves or dispatches.

        Every question parked off a plan carries ``PLAN_REVIEW`` as its source,
        so owning the source alone made the first answer approve the plan,
        dispatch its children with a question still open, and leave the gate's
        own approval PENDING with nobody having decided it.

        It is owned here rather than declined, because declining sends it on to
        the flows below, which read it as a task-completion review and refuse
        it: the operator's answer then rolls back with a 409 and reaches
        nothing.
        """
        state, _, backend = await _seed(
            action_type=question_type,
            task=_task("parent-1"),
            plan=_durable_plan("parent-1", open_questions=(_QUESTION,)),
        )

        handled = await try_plan_review_resume(
            state,
            sid("appr-1"),
            approved=True,
            decided_by="Aurelio",
            decision_reason="Postgres, please.",
        )

        assert handled is True
        await state.drain_entry_background_tasks()
        assert await _filed_children(backend) == []
        stored = await backend.plans.get(NotBlankStr(str(as_uuid(_PLAN_ID))))
        assert stored is not None
        assert stored.status is PlanStatus.PENDING_REVIEW
        assert stored.open_questions == ()
        assert any("Postgres, please." in a for a in stored.assumptions)

    async def test_a_declined_question_settles_without_an_answer(self) -> None:
        """A decline is a decision, and the plan records it as one."""
        state, _, backend = await _seed(
            action_type=CLARIFY_ACTION_TYPE,
            task=_task("parent-1"),
            plan=_durable_plan("parent-1", open_questions=(_QUESTION,)),
        )

        handled = await try_plan_review_resume(
            state,
            sid("appr-1"),
            approved=False,
            decided_by="Aurelio",
            decision_reason="The operator declined to answer this question.",
        )

        assert handled is True
        stored = await backend.plans.get(NotBlankStr(str(as_uuid(_PLAN_ID))))
        assert stored is not None
        assert stored.open_questions == ()
        # The plan records the decline in its OWN words, telling the agents to
        # proceed on the planner's judgement. The server-owned audit sentence
        # is not smuggled in as though it were the operator's answer.
        assert any(
            "the plan proceeds on the planner's own judgement" in a
            for a in stored.assumptions
        )
        assert not any(
            "The operator declined to answer this question." in a
            for a in stored.assumptions
        )

    async def test_approve_stages_the_durable_plan(self) -> None:
        parent = _task("parent-1")
        state, _, backend = await _seed(task=parent, plan=_durable_plan("parent-1"))
        rollup = mock_of[ProjectRollupService]()
        state.wire(EngineStateSlice, project_rollup_service=rollup)

        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )

        assert handled is True
        # The stage is opened behind the response, so the drain is what makes it
        # observable here; without it the request has already returned.
        await state.drain_entry_background_tasks()
        # Approval hands the plan to the rollup and nothing else. Dispatching
        # the units here as well would put them against a contract that does not
        # exist yet, which is the whole reason the stage was added.
        rollup.recompute.assert_awaited_once_with(as_uuid(_PLAN_ID))
        # The durable plan's items are rebuilt into filed child rows, which is
        # where every later reader looks for a plan's work.
        filed = await _filed_children(backend)
        assert {str(child.plan_item_id) for child in filed} == set(_SUB_IDS)
        # Each item's declared deliverable survives the rebuild onto its own
        # row, so the task's fail-loud zero-artifact guard stays armed rather
        # than silently disarmed by a dropped or swapped mapping.
        assert {
            str(child.plan_item_id): tuple(
                expected.path for expected in child.artifacts_expected
            )
            for child in filed
        } == {
            subtask_id: (NotBlankStr(f"src/part_{n}.py"),)
            for n, subtask_id in enumerate(_SUB_IDS)
        }
        # Rebuilt child tasks are fresh CREATED work parented on the objective.
        assert all(child.status is TaskStatus.CREATED for child in filed)
        # Every filed task carries its plan linkage, so the rollup can find a
        # plan's tasks without re-deriving the id mapping.
        assert all(child.plan_id == as_uuid(_PLAN_ID) for child in filed)
        # Approval stages the plan: it moves past the decision into the contract
        # stage rather than resting on the recorded verdict, and it does NOT
        # reach EXECUTING, which only a passing contract earns.
        stored = await backend.plans.get(NotBlankStr(str(as_uuid(_PLAN_ID))))
        assert stored is not None
        assert stored.status is PlanStatus.SKELETON

    async def test_no_coordinator_is_needed_to_stage_a_plan(self) -> None:
        """Approval runs no wave, so it must not fail one for a missing driver.

        The rollup drives the waves once the contract passes, and resolves its
        own coordinator when it does. Treated as a precondition here, an unwired
        one would fail an initiative over a subsystem the approval path never
        reaches. Asserted against the slice so the seed's silence about
        coordinators is a stated fact rather than an omission a later edit can
        quietly undo.
        """
        state, engine, backend = await _seed(
            task=_task("parent-1"), plan=_durable_plan("parent-1")
        )
        assert state.slice(RuntimeStateSlice).coordinator is None

        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )
        await state.drain_entry_background_tasks()

        assert handled is True
        stored = await backend.plans.get(NotBlankStr(str(as_uuid(_PLAN_ID))))
        assert stored is not None
        assert stored.status is PlanStatus.SKELETON
        assert _transitions_of(engine, str(as_uuid("parent-1"))) == []

    async def test_an_unclicked_decision_is_recorded_on_the_plan_it_dispatches(
        self,
    ) -> None:
        """Approving without picking IS a decision, and the plan must say so.

        The rebuild already treats it as made: ``decomposition_from_plan``
        strips the decision out of every dependent's dependencies on those exact
        grounds. Completion asks a different question -- is ``chosen_option_id``
        set -- so leaving it unwritten gives one decision two owners that
        disagree, and the initiative can dispatch every item and still never
        finish. Both of run 6's live plans were in that state.
        """
        parent = _task("parent-1")
        state, _, backend = await _seed(
            task=parent, plan=_durable_plan("parent-1", with_decision=True)
        )

        await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )
        await state.drain_entry_background_tasks()

        stored = await backend.plans.get(NotBlankStr(str(as_uuid(_PLAN_ID))))
        assert stored is not None
        decision = next(i for i in stored.items if i.kind is PlanItemKind.DECISION)
        assert decision.chosen_option_id == _RECOMMENDED_OPTION
        # The work items are still filed, with the decision stripped from their
        # dependencies -- which is only honest now that the plan records it.
        filed = await _filed_children(backend)
        assert {str(child.plan_item_id) for child in filed} == set(_SUB_IDS)

    async def test_an_operators_own_pick_is_never_overwritten(self) -> None:
        """The recommendation is the fallback, not the answer."""
        parent = _task("parent-1")
        plan = _durable_plan("parent-1", with_decision=True)
        chosen = tuple(
            i.model_copy(update={"chosen_option_id": NotBlankStr("framework")})
            if i.kind is PlanItemKind.DECISION
            else i
            for i in plan.items
        )
        state, _, backend = await _seed(
            task=parent, plan=plan.model_copy(update={"items": chosen})
        )

        await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )

        stored = await backend.plans.get(NotBlankStr(str(as_uuid(_PLAN_ID))))
        assert stored is not None
        decision = next(i for i in stored.items if i.kind is PlanItemKind.DECISION)
        assert decision.chosen_option_id == "framework"

    async def test_approve_links_and_activates_the_project(self) -> None:
        """The graph is connected before any dispatched task can run."""
        parent = _task("parent-1")
        state, _, backend = await _seed(task=parent, plan=_durable_plan("parent-1"))

        await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )

        project = await backend.projects.get(NotBlankStr(sid("proj-1")))
        assert project is not None
        assert project.plan_id == as_uuid(_PLAN_ID)
        assert project.status is ProjectStatus.ACTIVE

    async def test_an_unlinkable_project_refuses_the_staging(self) -> None:
        """Staging against a project that never learned its plan is worse than
        not staging: the work is filed, but its progress view reports no plan
        and its status can only advance by an illegal jump.
        """
        parent = _task("parent-1")
        state, _, backend = await _seed(
            task=parent,
            plan=_durable_plan("parent-1"),
            save_project=False,
        )
        rollup = mock_of[ProjectRollupService]()
        state.wire(EngineStateSlice, project_rollup_service=rollup)

        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )
        await state.drain_entry_background_tasks()

        assert handled is True
        assert await _filed_children(backend) == []
        rollup.recompute.assert_not_awaited()

    async def test_reject_cancels_task_and_marks_plan_rejected(self) -> None:
        parent = _task("parent-1")
        state, engine, backend = await _seed(
            task=parent, plan=_durable_plan("parent-1")
        )
        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=False, decided_by="admin"
        )
        assert handled is True
        assert await _filed_children(backend) == []
        engine.transition_task.assert_awaited_once()
        call = engine.transition_task.await_args
        assert call.args[0] == str(parent.id)
        assert call.args[1] is TaskStatus.CANCELLED
        stored = await backend.plans.get(NotBlankStr(str(as_uuid(_PLAN_ID))))
        assert stored is not None
        assert stored.status is PlanStatus.REJECTED

    async def test_sync_plan_status_aborts_on_raced_deletion(self) -> None:
        # The plan is present on the initial fetch but gone on the CAS re-read
        # (a delete raced the status sync). The loop must abort cleanly on the
        # not-found rather than spin its retries against the stale plan into a
        # misleading version-conflict error log.
        plan = _durable_plan("parent-1")
        state, _, backend = await _seed(task=_task("parent-1"), plan=plan)
        scripted_get = AsyncMock(side_effect=[plan, None])
        backend.plans.get = scripted_get  # type: ignore[method-assign]
        await sync_plan_status(state, str(plan.id), PlanStatus.APPROVED)
        # Exactly two reads: the initial fetch plus one CAS read that saw the
        # deletion and aborted. A retry against the stale plan would read again.
        assert scripted_get.await_count == 2

    async def test_missing_task_marks_task_failed(self) -> None:
        # The approval references a task that no longer exists (get_task -> None):
        # the flow owns the decision but the parent task is marked FAILED so the
        # stuck plan surfaces rather than sitting silently in pre-approval status.
        state, engine, backend = await _seed(
            task=None,
            plan=_durable_plan("parent-1"),
            approval_task_id=str(as_uuid("parent-1")),
        )
        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )
        assert handled is True
        assert await _filed_children(backend) == []
        engine.transition_task.assert_awaited_once()
        assert engine.transition_task.await_args.args[1] is TaskStatus.FAILED

    async def test_missing_plan_marks_task_failed(self) -> None:
        # The approval references a plan_id that is not persisted: the flow marks
        # the parent task FAILED rather than returning a silent no-op.
        parent = _task("parent-1")
        state, engine, _ = await _seed(
            task=parent, plan=_durable_plan("parent-1"), save_plan=False
        )
        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )
        assert handled is True
        engine.transition_task.assert_awaited_once()
        assert engine.transition_task.await_args.args[1] is TaskStatus.FAILED

    async def test_staging_failure_fails_both_the_task_and_the_plan(
        self,
    ) -> None:
        # A staging failure must not 5xx the approval-decision request: the flow
        # still owns the decision (True) and marks the task FAILED. The plan
        # leaves SKELETON too: staging moves it there before filing the task
        # tree, so a failure would otherwise leave it SKELETON forever with a
        # failed parent and no children, which nothing watches and nothing can
        # move.
        parent = _task("parent-1")
        state, engine, backend = await _seed(
            task=parent,
            plan=_durable_plan("parent-1"),
            filing_error=RuntimeError("boom"),
        )
        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )
        assert handled is True
        await state.drain_entry_background_tasks()
        assert _transitions_of(engine, str(as_uuid("parent-1"))) == [TaskStatus.FAILED]
        stored = await backend.plans.get(NotBlankStr(str(as_uuid(_PLAN_ID))))
        assert stored is not None
        assert stored.status is PlanStatus.FAILED
        # Plan Review shows the reason rather than an unexplained failure.
        assert stored.failure_reason is not None
        assert "dispatch failed" in stored.failure_reason

    async def test_a_failed_staging_opens_no_contract_stage(self) -> None:
        """Nothing is handed on once the graph could not be made durable.

        The contract job reads the plan's work out of the repository, so
        opening the stage against a tree that was never filed asks an agent to
        write a contract for units that do not exist, and the plan is FAILED
        underneath it while the job runs.
        """
        state, _, _ = await _seed(
            task=_task("parent-1"),
            plan=_durable_plan("parent-1"),
            filing_error=RuntimeError("boom"),
        )
        rollup = mock_of[ProjectRollupService]()
        state.wire(EngineStateSlice, project_rollup_service=rollup)

        await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )
        await state.drain_entry_background_tasks()

        rollup.recompute.assert_not_awaited()

    async def test_a_rollup_that_fails_leaves_the_plan_for_the_recovery_sweep(
        self,
    ) -> None:
        """A stage that could not be opened is late, not lost.

        Opening the stage is the one thing approval does not finish before it
        answers, and failing the plan on it would be actively wrong: SKELETON
        is a stage status, the recovery sweep recomputes every plan sitting in
        one on its cadence, and FAILED is terminal, so failing it converts a
        delay somebody can see into an initiative nothing will ever look at
        again.
        """
        parent = _task("parent-1")
        state, engine, backend = await _seed(
            task=parent, plan=_durable_plan("parent-1")
        )
        rollup = mock_of[ProjectRollupService](
            recompute=AsyncMock(side_effect=QueryError("rollup store offline"))
        )
        state.wire(EngineStateSlice, project_rollup_service=rollup)

        await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )
        await state.drain_entry_background_tasks()

        rollup.recompute.assert_awaited_once()
        assert _transitions_of(engine, str(as_uuid("parent-1"))) == []
        stored = await backend.plans.get(NotBlankStr(str(as_uuid(_PLAN_ID))))
        assert stored is not None
        assert stored.status is PlanStatus.SKELETON

    async def test_a_stage_opening_cancelled_at_shutdown_does_not_strand_the_plan(
        self,
    ) -> None:
        """Cancellation needs no compensation of its own.

        The plan is already durable at SKELETON when the background task
        starts, so a shutdown drain that kills the recompute leaves a plan the
        recovery sweep picks up and re-drives. Compensating here instead would
        fail an
        initiative for the sake of a restart, which is an ordinary operator
        action.
        """
        parent = _task("parent-1")
        state, engine, backend = await _seed(
            task=parent, plan=_durable_plan("parent-1")
        )
        rollup = mock_of[ProjectRollupService](
            recompute=AsyncMock(side_effect=asyncio.CancelledError())
        )
        state.wire(EngineStateSlice, project_rollup_service=rollup)

        await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )
        await state.drain_entry_background_tasks()

        stored = await backend.plans.get(NotBlankStr(str(as_uuid(_PLAN_ID))))
        assert stored is not None
        assert stored.status is PlanStatus.SKELETON
        assert _transitions_of(engine, str(as_uuid("parent-1"))) == []

    async def test_the_approve_call_returns_before_the_stage_opens(self) -> None:
        """The whole point of backgrounding it.

        Awaiting the stage inside the request holds the approve call open for
        the length of a contract job, which runs into the minutes even on a
        small plan: the client gives up while the server carries on, and the
        operator is told a decision failed that was recorded.
        """
        state, _, backend = await _seed(
            task=_task("parent-1"), plan=_durable_plan("parent-1")
        )
        rollup = mock_of[ProjectRollupService]()
        state.wire(EngineStateSlice, project_rollup_service=rollup)

        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )

        assert handled is True
        rollup.recompute.assert_not_awaited()
        # The decision's own consequences are durable before the response, so
        # the operator is never told less than the board already knows.
        stored = await backend.plans.get(NotBlankStr(str(as_uuid(_PLAN_ID))))
        assert stored is not None
        assert stored.status is PlanStatus.SKELETON
        assert len(await _filed_children(backend)) == len(_SUB_IDS)

        await state.drain_entry_background_tasks()

        rollup.recompute.assert_awaited_once()

    @pytest.mark.parametrize(
        "branch",
        [
            pytest.param(_PreconditionBranch(task=None), id="no_parent_task"),
            pytest.param(
                _PreconditionBranch(task=_task("parent-1"), save_project=False),
                id="project_not_linkable",
            ),
        ],
    )
    async def test_a_precondition_failure_also_fails_the_plan(
        self, branch: _PreconditionBranch
    ) -> None:
        """A plan that cannot be staged must not rest in a decided status.

        Every precondition branch returns before the stage is opened, so the
        plan sits in APPROVED with nothing left to advance it unless it is
        failed here. Parametrised over both because they are separate early
        returns through one helper, and a branch added later that forgets the
        plan write looks identical from the task's side.
        """
        state, _, backend = await _seed(plan=_durable_plan("parent-1"), **branch)

        await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )

        stored = await backend.plans.get(NotBlankStr(str(as_uuid(_PLAN_ID))))
        assert stored is not None
        assert stored.status is PlanStatus.FAILED
        assert stored.failure_reason is not None


def _ledger(backend: FakePersistenceBackend) -> dict[PlanStatus, tuple[str, str]]:
    """Map each recorded plan status to its ``(actor, reason)`` pair.

    Returns:
        One entry per transition the ledger holds, keyed by destination.
    """
    return {
        PlanStatus(row.to_status): (row.requested_by or "", row.reason or "")
        for row in backend.lifecycle_transitions.transitions
        if row.entity_kind is LifecycleEntityKind.PLAN
    }


class TestWhoTheLedgerNames:
    """The one question the ledger exists to answer: who did this.

    An operator searches it by their own name. Recording a dispatch failure
    against the approver puts a transition they never made under that name,
    and leaves the decision they did make attributed to nobody, so the ledger
    answers the question backwards on both rows.
    """

    async def test_the_operator_owns_the_decision_they_made(self) -> None:
        state, _, backend = await _seed(
            task=_task("parent-1"), plan=_durable_plan("parent-1")
        )

        await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="Aurelio"
        )

        actor, _reason = _ledger(backend)[PlanStatus.APPROVED]
        assert actor == "Aurelio"

    async def test_the_operator_owns_a_rejection(self) -> None:
        state, _, backend = await _seed(
            task=_task("parent-1"), plan=_durable_plan("parent-1")
        )

        await try_plan_review_resume(
            state, sid("appr-1"), approved=False, decided_by="Aurelio"
        )

        actor, _reason = _ledger(backend)[PlanStatus.REJECTED]
        assert actor == "Aurelio"

    async def test_the_dispatcher_owns_what_follows_the_decision(self) -> None:
        """Entering the contract stage is the system acting on the greenlight."""
        state, _, backend = await _seed(
            task=_task("parent-1"), plan=_durable_plan("parent-1")
        )

        await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="Aurelio"
        )

        actor, _reason = _ledger(backend)[PlanStatus.SKELETON]
        assert actor == _DISPATCH_ACTOR

    async def test_a_failed_staging_names_the_dispatcher_and_says_why(self) -> None:
        """The row an operator reads months later, on the run that died."""
        state, _, backend = await _seed(
            task=_task("parent-1"),
            plan=_durable_plan("parent-1"),
            filing_error=QueryError("task store offline"),
        )

        await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="Aurelio"
        )
        await state.drain_entry_background_tasks()

        actor, reason = _ledger(backend)[PlanStatus.FAILED]
        assert actor == _DISPATCH_ACTOR
        assert "dispatch failed" in reason
